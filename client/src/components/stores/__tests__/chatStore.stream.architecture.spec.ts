import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useChatStore } from '../chatStore';
import { useDirectorAutoWriteStore } from '../directorAutoWriteStore';
import { cancelChatTask, compactChatContext, getChatHistory, getChatTaskStatus } from '@/services/chatService';
import bus from '@/eventBus';

vi.mock('@/components/stores/projectStore', () => ({
  useProjectStore: () => ({ currentProject: '测试项目' }),
}));
vi.mock('@/services/chatService', async () => {
  const actual = await vi.importActual<typeof import('@/services/chatService')>('@/services/chatService');
  return {
    ...actual,
    compactChatContext: vi.fn(),
    getChatHistory: vi.fn(),
    getChatTaskStatus: vi.fn(),
    cancelChatTask: vi.fn(),
  };
});

function readerFromEvents(events: Record<string, unknown>[]): ReadableStreamDefaultReader<Uint8Array> {
  const encoder = new TextEncoder();
  const lines = events.map(event => `${JSON.stringify(event)}\n`);
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      const line = lines.shift();
      if (line) {
        controller.enqueue(encoder.encode(line));
      } else {
        controller.close();
      }
    },
  }).getReader();
}

type ConsumeStreamState = {
  agentId: string;
  contextKey: string;
  streamEpoch: number;
  lastSeq?: number;
  receivedTaskDone?: boolean;
  userMessageClientId?: string;
  userMessageContent?: string;
};

describe('chatStore NDJSON 消费契约', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.clearAllMocks();
  });

  it('切换 Agent 只切换可见主会话，后台流状态与历史保持隔离', () => {
    const store = useChatStore();
    const directorSession = store.primarySession;
    directorSession.sending = true;
    directorSession.backgroundTaskStatus = 'running';
    directorSession.history = [{ role: 'assistant', content: '导演仍在输出' }];

    store.setAgent('agent_scriptwriter');
    const scriptwriterSession = store.primarySession;
    expect(scriptwriterSession.id).not.toBe(directorSession.id);
    expect(scriptwriterSession.sending).toBe(false);
    expect(scriptwriterSession.history).toEqual([]);
    expect(store.runningAgentIds.has('agent_director')).toBe(true);

    scriptwriterSession.sending = true;
    scriptwriterSession.backgroundTaskStatus = 'running';
    scriptwriterSession.history = [{ role: 'user', content: '编剧会话' }];
    expect(store.runningAgentIds).toEqual(new Set(['agent_director', 'agent_scriptwriter']));

    store.setAgent('agent_director');
    expect(store.primarySession.id).toBe(directorSession.id);
    expect(store.history).toEqual([{ role: 'assistant', content: '导演仍在输出' }]);
    expect(store.sending).toBe(true);
  });

  it('断开本地观察流不会隐式取消后端任务', () => {
    const store = useChatStore();
    const session = store.primarySession;
    session.sending = true;
    session.backgroundTaskStatus = 'running';

    store._invalidateSessionStream(session.id);

    expect(cancelChatTask).not.toHaveBeenCalled();
    expect(session.sending).toBe(false);
    expect(session.backgroundTaskStatus).toBeNull();
  });

  it('显式取消成功后立即中断本地流并解除发送锁', async () => {
    vi.mocked(cancelChatTask).mockResolvedValueOnce({ success: true });
    vi.mocked(getChatTaskStatus).mockResolvedValueOnce({ hasTask: true, status: 'cancelled' });
    const store = useChatStore();
    const session = store.primarySession;
    session.sending = true;
    session.backgroundTaskStatus = 'running';
    session.abortController = new AbortController();

    await store.cancelSessionRequest(session.id);

    expect(cancelChatTask).toHaveBeenCalledWith('测试项目', 'agent_director', 'global');
    expect(session.abortController.signal.aborted).toBe(true);
    expect(session.sending).toBe(false);
    expect(session.backgroundTaskStatus).toBeNull();

    await vi.advanceTimersByTimeAsync(250);
    expect(session.sending).toBe(false);
    expect(session.backgroundTaskStatus).toBeNull();
  });

  it('取消成功后观察流无终态时以权威状态查询收口', async () => {
    vi.mocked(cancelChatTask).mockResolvedValueOnce({ success: true });
    vi.mocked(getChatTaskStatus)
      .mockResolvedValueOnce({ hasTask: true, status: 'running' })
      .mockResolvedValueOnce({ hasTask: true, status: 'cancelled' });
    const store = useChatStore();
    const session = store.primarySession;
    session.sending = true;
    session.backgroundTaskStatus = 'running';
    session.streamEpoch = 1;
    session.abortController = new AbortController();

    await store.cancelSessionRequest(session.id);
    expect(session.sending).toBe(false);
    expect(session.backgroundTaskStatus).toBeNull();

    await vi.advanceTimersByTimeAsync(250);
    expect(session.sending).toBe(false);
    expect(session.backgroundTaskStatus).toBeNull();

    await vi.advanceTimersByTimeAsync(500);
    expect(session.sending).toBe(false);
    expect(session.backgroundTaskStatus).toBeNull();
    expect(getChatHistory).toHaveBeenCalledWith('测试项目', 'agent_director', 'global', 80);
  });

  it('取消后立即收口本地状态且迟到 task_done 不会重新进入运行态', async () => {
    vi.mocked(cancelChatTask).mockResolvedValueOnce({ success: true });
    const store = useChatStore();
    const session = store.primarySession;
    session.projectName = '测试项目';
    session.sending = true;
    session.backgroundTaskStatus = 'running';
    session.streamEpoch = 1;
    session.abortController = new AbortController();

    let streamController: ReadableStreamDefaultController<Uint8Array> | null = null;
    const reader = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
    }).getReader();
    const consumePromise = store._consumeStream(
      session,
      {
        clientId: 'assistant-late-cancel',
        role: 'assistant',
        content: '',
        reasoning: '',
        tool_traces: [],
        segments: [],
        timestamp: 1,
      },
      false,
      reader,
      session.id,
      {
        projectName: '测试项目',
        agentId: 'agent_director',
        contextKey: 'global',
        streamEpoch: 1,
      },
    );

    await store.cancelSessionRequest(session.id);
    expect(session.sending).toBe(false);
    expect(session.backgroundTaskStatus).toBeNull();

    streamController!.enqueue(new TextEncoder().encode(`${JSON.stringify({
      event: 'task_done',
      seq: 1,
      status: 'cancelled',
    })}\n`));
    await vi.waitFor(() => {
      expect(session.sending).toBe(false);
      expect(session.backgroundTaskStatus).toBeNull();
    });

    await consumePromise;
    await vi.advanceTimersByTimeAsync(250);
  });

  it('取消请求失败时保持运行锁并提示错误', async () => {
    vi.mocked(cancelChatTask).mockRejectedValueOnce(new Error('取消任务失败'));
    const store = useChatStore();
    const session = store.primarySession;
    session.sending = true;
    session.backgroundTaskStatus = 'running';
    const toasts: any[] = [];
    const onToast = (payload: any) => toasts.push(payload);
    bus.on('toast', onToast);

    await store.cancelSessionRequest(session.id);

    bus.off('toast', onToast);
    expect(session.sending).toBe(false);
    expect(session.backgroundTaskStatus).toBeNull();
    expect(toasts.at(-1)?.type).toBe('error');
  });

  it('发送前发现后端已有任务时恢复观察且不追加乐观消息', async () => {
    vi.mocked(getChatTaskStatus).mockResolvedValueOnce({
      hasTask: true,
      status: 'running',
      agentId: 'agent_director',
      contextKey: 'global',
      lastSeq: 8,
    });
    const store = useChatStore();
    const session = store.primarySession;
    const reconnectSpy = vi.spyOn(store, '_reconnectTaskStream').mockResolvedValueOnce(undefined);

    await store.sendSessionMessage(session.id, '新消息');

    expect(session.history).toEqual([]);
    expect(session.sending).toBe(true);
    expect(session.backgroundTaskStatus).toBe('running');
    expect(reconnectSpy).toHaveBeenCalledWith(session, 'agent_director', 'global');
  });

  it('切换项目后旧项目流只更新其绑定会话，返回时仍可继续查看', async () => {
    const store = useChatStore();
    store.switchProject('huang');
    const huangSession = store.primarySession;
    huangSession.streamEpoch = 1;

    let streamController: ReadableStreamDefaultController<Uint8Array> | null = null;
    const reader = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
    }).getReader();
    const assistantMsg: any = {
      clientId: 'assistant-huang',
      role: 'assistant',
      content: '',
      reasoning: '',
      tool_traces: [],
      segments: [],
      timestamp: 1,
    };
    const consumePromise = store._consumeStream(huangSession, assistantMsg, false, reader, huangSession.id, {
      projectName: 'huang',
      agentId: 'agent_director',
      contextKey: 'global',
      streamEpoch: 1,
    });

    store.switchProject('123');
    const project123Session = store.primarySession;
    project123Session.history = [{ role: 'user', content: '123 项目的消息' }];
    expect(project123Session.id).not.toBe(huangSession.id);

    const encoder = new TextEncoder();
    streamController!.enqueue(encoder.encode(`${JSON.stringify({ event: 'assistant_delta', seq: 1, text: 'huang 项目的回复' })}\n`));
    streamController!.enqueue(encoder.encode(`${JSON.stringify({ event: 'task_done', seq: 2, status: 'completed' })}\n`));
    streamController!.close();
    await consumePromise;

    expect(store.history).toEqual([{ role: 'user', content: '123 项目的消息' }]);
    expect(huangSession.history.at(-1)?.content).toBe('huang 项目的回复');

    store.switchProject('huang');
    expect(store.primarySession.id).toBe(huangSession.id);
    expect(store.history.at(-1)?.content).toBe('huang 项目的回复');

    vi.mocked(getChatHistory).mockResolvedValueOnce([{ role: 'assistant', content: 'huang 已落盘' }] as any);
    store.switchProject('123');
    await store.refreshSessionHistory(huangSession.id);
    expect(getChatHistory).toHaveBeenLastCalledWith('huang', 'agent_director', 'global', 80);
  });

  it('删除项目后释放该项目的本地会话，同名重建不会复用旧历史', async () => {
    const store = useChatStore();
    store.switchProject('同名项目');
    const staleSession = store.primarySession;
    staleSession.history = [{ role: 'user', content: '旧聊天' }];

    store.switchProject('其他项目');
    store.purgeProjectSessions('同名项目');

    expect(store.sessions[staleSession.id]).toBeUndefined();
    expect(
      Object.keys(store.primarySessionBindings).some((key) => key.split('::')[0] === '同名项目'),
    ).toBe(false);

    store.switchProject('同名项目');
    expect(store.primarySession.id).not.toBe(staleSession.id);
    expect(store.history).toEqual([]);
  });

  it('历史刷新从最终失败助手消息恢复后台错误展示', async () => {
    vi.mocked(getChatHistory).mockResolvedValueOnce([
      { id: 1, role: 'user', content: '继续执行' },
      {
        id: 2,
        role: 'assistant',
        content: '已生成部分内容',
        metadata: {
          stream_status: 'error',
          error: 'Director 图达到递归步数上限',
          retry_count: 2,
        },
      },
    ] as any);
    const store = useChatStore();
    const session = store.primarySession;

    await store.refreshSessionHistory(session.id, 80, { authoritative: true });

    expect(session.lastError).toBe('Director 图达到递归步数上限');
  });

  it('历史刷新不会重新展示已被后续用户消息覆盖的旧错误', async () => {
    vi.mocked(getChatHistory).mockResolvedValueOnce([
      {
        id: 1,
        role: 'assistant',
        content: '上次部分内容',
        metadata: {
          stream_status: 'error',
          error: '上次请求失败',
        },
      },
      { id: 2, role: 'user', content: '请重新开始' },
    ] as any);
    const store = useChatStore();
    const session = store.primarySession;

    await store.refreshSessionHistory(session.id, 80, { authoritative: true });

    expect(session.lastError).toBe('');
  });

  it('消费 task_snapshot / delta / tool 事件并维护 segments 与 tool_traces', async () => {
    const store = useChatStore();
    const session = store.primarySession;
    session.sending = true;
    session.backgroundTaskStatus = 'running';
    session.streamEpoch = 1;
    session.history = [{ clientId: 'user-local', role: 'user', content: '修正后的问题' }];

    const assistantMsg: any = {
      clientId: 'assistant-local',
      role: 'assistant',
      content: '',
      reasoning: '',
      tool_traces: [],
      segments: [],
      timestamp: 1,
    };

    const reader = readerFromEvents([
      {
        event: 'task_snapshot',
        status: 'running',
        seq: 1,
        task_id: 'task-1',
        assistant_message_id: 99,
        user_message_id: 98,
        content: '旧正文',
        reasoning: '旧推理',
        segments: [{ type: 'text', text: '旧正文', source_agent: 'agent_director' }],
        tool_traces: [],
        metadata: { stream_seq: 1 },
      },
      { event: 'reasoning_delta', seq: 2, text: '新推理', source_agent: 'agent_director' },
      { event: 'assistant_delta', seq: 3, text: '新正文', source_agent: 'agent_director' },
      {
        event: 'tool_intent_started',
        seq: 4,
        tool_name: 'rewrite_worldview',
        tool_call_key: 'call-1',
        tool_input: { content: '新的世界观' },
        message: '准备修改世界观',
        ui_scope: 'world',
        ui_target: 'worldview',
        ui_refresh_events: ['lorebook-refresh-worldview'],
      },
      {
        event: 'tool_exec_started',
        seq: 5,
        tool_name: 'rewrite_worldview',
        tool_call_key: 'call-1',
      },
      {
        event: 'tool_exec_finished',
        seq: 6,
        tool_name: 'rewrite_worldview',
        tool_call_key: 'call-1',
        tool_result: '完成',
      },
      {
        event: 'context_window_stats',
        seq: 7,
        agent_id: 'agent_director',
        input_tokens: 1200,
        output_tokens: 0,
        cached_prompt_tokens: 900,
        cache_miss_prompt_tokens: 300,
        cache_hit_rate: 0.75,
        original_tokens: 1200,
        retained_messages: 2,
        model: 'fake-model',
        compacted: false,
        reason: 'within_budget',
      },
      {
        event: 'task_done',
        seq: 8,
        status: 'completed',
        metadata: { stream_status: 'completed', stream_seq: 8 },
        llm_usage: {
          prompt_tokens: 1200,
          completion_tokens: 120,
          total_tokens: 1320,
          cached_prompt_tokens: 900,
          cache_miss_prompt_tokens: 300,
          by_agent: {
            agent_director: {
              completion_tokens: 120,
              cached_prompt_tokens: 900,
              cache_miss_prompt_tokens: 300,
              cache_hit_rate: 0.75,
            },
          },
        },
        context_window_stats: {
          agent_id: 'agent_director',
          input_tokens: 1200,
          output_tokens: 120,
          cached_prompt_tokens: 900,
          cache_miss_prompt_tokens: 300,
          cache_hit_rate: 0.75,
          original_tokens: 1200,
          retained_messages: 2,
          model: 'fake-model',
          compacted: false,
          reason: 'within_budget',
        },
      },
    ]);

    const streamState: ConsumeStreamState = {
      agentId: 'agent_director',
      contextKey: 'global',
      streamEpoch: 1,
      userMessageClientId: 'user-local',
      userMessageContent: '修正后的问题',
    };
    await store._consumeStream(session, assistantMsg, false, reader, 0, streamState);

    expect(assistantMsg.id).toBe(99);
    expect(session.history[0]).toMatchObject({ clientId: 'user-local', id: 98, content: '修正后的问题' });
    expect(assistantMsg.task_id).toBe('task-1');
    expect(assistantMsg.content).toBe('旧正文新正文');
    expect(assistantMsg.reasoning).toBe('旧推理新推理');
    expect(assistantMsg.streamSeq).toBe(3);
    expect(streamState.lastSeq).toBe(8);
    expect(assistantMsg.tool_traces).toHaveLength(1);
    expect(assistantMsg.tool_traces[0]).toMatchObject({
      tool_name: 'rewrite_worldview',
      status: 'finished',
      tool_call_key: 'call-1',
      tool_input: { content: '新的世界观' },
    });
    expect(assistantMsg.segments.map((segment: any) => segment.type)).toEqual([
      'text',
      'reasoning',
      'text',
      'tool_trace',
    ]);
    expect(assistantMsg.segments[3]).toMatchObject({
      tool_name: 'rewrite_worldview',
      status: 'finished',
      tool_input: { content: '新的世界观' },
      tool_result: '完成',
    });
    expect(session.sending).toBe(false);
    expect(session.backgroundTaskStatus).toBeNull();
    expect(session.contextWindowStats).toMatchObject({
      agentId: 'agent_director',
      inputTokens: 1200,
      outputTokens: 120,
      cachedPromptTokens: 900,
      cacheMissPromptTokens: 300,
      cacheHitRate: 0.75,
    });
    expect(assistantMsg.metadata.context_window_stats).toMatchObject({
      cached_prompt_tokens: 900,
      cache_miss_prompt_tokens: 300,
      cache_hit_rate: 0.75,
    });

    vi.runOnlyPendingTimers();
  });

  it('task_done 为 cancelled 时立即清除发送锁并记录真实终态', async () => {
    const store = useChatStore();
    const session = store.primarySession;
    session.sending = true;
    session.backgroundTaskStatus = 'running';
    session.streamEpoch = 1;
    const assistantMsg: any = {
      clientId: 'assistant-cancelled',
      role: 'assistant',
      content: '',
      reasoning: '',
      tool_traces: [],
      segments: [],
      timestamp: 1,
    };
    const streamState: ConsumeStreamState = {
      agentId: 'agent_director',
      contextKey: 'global',
      streamEpoch: 1,
    };

    await store._consumeStream(
      session,
      assistantMsg,
      false,
      readerFromEvents([{ event: 'task_done', seq: 1, status: 'cancelled' }]),
      session.id,
      streamState,
    );

    expect(streamState.receivedTaskDone).toBe(true);
    expect(session.sending).toBe(false);
    expect(session.backgroundTaskStatus).toBeNull();
  });

  it('task_done 到达时立即撤销工具遮罩，不等待观察流关闭', async () => {
    const store = useChatStore();
    const session = store.primarySession;
    session.sending = true;
    session.backgroundTaskStatus = 'running';
    session.streamEpoch = 1;
    const loadingEvents: any[] = [];
    const onLoading = (payload: any) => loadingEvents.push(payload);
    bus.on('global-loading', onLoading);

    let streamController: ReadableStreamDefaultController<Uint8Array> | null = null;
    const reader = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
    }).getReader();
    const assistantMsg: any = {
      clientId: 'assistant-tool-terminal',
      role: 'assistant',
      content: '',
      reasoning: '',
      tool_traces: [],
      segments: [],
      timestamp: 1,
    };
    const consumePromise = store._consumeStream(session, assistantMsg, false, reader, session.id, {
      agentId: 'agent_director',
      contextKey: 'global',
      streamEpoch: 1,
    });
    const encoder = new TextEncoder();
    streamController!.enqueue(encoder.encode(`${JSON.stringify({
      event: 'tool_intent_started',
      tool_name: 'rewrite_worldview',
      tool_call_key: 'intent-key',
      ui_scope: 'world',
      ui_target: 'worldview',
    })}\n`));
    streamController!.enqueue(encoder.encode(`${JSON.stringify({
      event: 'tool_exec_started',
      tool_name: 'rewrite_worldview',
      tool_call_key: 'exec-key',
      ui_scope: 'world',
      ui_target: 'worldview',
    })}\n`));
    streamController!.enqueue(encoder.encode(`${JSON.stringify({
      event: 'task_done',
      status: 'completed',
    })}\n`));

    await vi.waitFor(() => {
      expect(loadingEvents).toContainEqual(expect.objectContaining({
        show: false,
        scope: 'world',
        target: 'worldview',
      }));
    });
    expect(session.toolCalling).toBe(false);
    expect(session.sending).toBe(false);

    streamController!.close();
    await consumePromise;
    bus.off('global-loading', onLoading);
  });

  it('tool_exec_failed 立即结束工具状态并撤销遮罩', async () => {
    const store = useChatStore();
    const session = store.primarySession;
    session.sending = true;
    session.backgroundTaskStatus = 'running';
    session.streamEpoch = 1;
    const loadingEvents: any[] = [];
    const onLoading = (payload: any) => loadingEvents.push(payload);
    bus.on('global-loading', onLoading);

    let streamController: ReadableStreamDefaultController<Uint8Array> | null = null;
    const reader = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
    }).getReader();
    const assistantMsg: any = {
      clientId: 'assistant-tool-failed',
      role: 'assistant',
      content: '',
      reasoning: '',
      tool_traces: [],
      segments: [],
      timestamp: 1,
    };
    const consumePromise = store._consumeStream(session, assistantMsg, false, reader, session.id, {
      agentId: 'agent_director',
      contextKey: 'global',
      streamEpoch: 1,
    });
    const encoder = new TextEncoder();
    for (const event of [
      {
        event: 'tool_exec_started',
        tool_name: 'patch_script',
        tool_call_key: 'patch-call',
        ui_scope: 'production',
        ui_target: '',
      },
      {
        event: 'tool_exec_failed',
        tool_name: 'patch_script',
        tool_call_key: 'patch-call',
        ui_scope: 'production',
        ui_target: '',
        tool_input: { search_text: '旧片段', replace_text: '新片段' },
        tool_error: '局部修改失败：未找到片段',
        message: '局部修改失败：未找到片段',
      },
    ]) {
      streamController!.enqueue(encoder.encode(`${JSON.stringify(event)}\n`));
    }

    await vi.waitFor(() => {
      expect(assistantMsg.tool_traces.at(-1)).toMatchObject({
        tool_name: 'patch_script',
        status: 'failed',
        tool_input: { search_text: '旧片段', replace_text: '新片段' },
        tool_error: '局部修改失败：未找到片段',
      });
      expect(loadingEvents).toContainEqual(expect.objectContaining({
        show: false,
        scope: 'production',
      }));
    });

    streamController!.close();
    await consumePromise;
    bus.off('global-loading', onLoading);
  });

  it('替换工作中消息时先取消服务端旧任务并等待真实终态', async () => {
    const store = useChatStore();
    const session = store.primarySession;
    const abort = vi.fn();
    session.abortController = { abort } as unknown as AbortController;
    vi.mocked(getChatTaskStatus)
      .mockResolvedValueOnce({ hasTask: true, status: 'running' })
      .mockResolvedValueOnce({ hasTask: true, status: 'cancelled' });
    vi.mocked(cancelChatTask).mockResolvedValue({ success: true });

    const replacement = store._cancelAndWaitForChatTaskReplacement(
      session,
      'agent_director',
      'global',
      '测试项目',
    );
    await vi.runAllTimersAsync();
    await replacement;

    expect(abort).toHaveBeenCalledWith('message_replaced');
    expect(cancelChatTask).toHaveBeenCalledWith('测试项目', 'agent_director', 'global');
    expect(getChatTaskStatus).toHaveBeenCalledTimes(2);
  });

  it('在 task_done 前消费实时 llm_usage 并更新任务累计用量', async () => {
    const store = useChatStore();
    const session = store.primarySession;
    session.sending = true;
    session.backgroundTaskStatus = 'running';
    session.streamEpoch = 1;
    const assistantMsg: any = {
      clientId: 'assistant-live-usage',
      role: 'assistant',
      content: '',
      reasoning: '',
      tool_traces: [],
      segments: [],
      timestamp: 1,
    };

    const reader = readerFromEvents([{
      event: 'llm_usage',
      seq: 1,
      llm_usage: {
        prompt_tokens: 1800,
        completion_tokens: 300,
        total_tokens: 2100,
        requests: 2,
        by_agent: {
          agent_director: {
            prompt_tokens: 1000,
            completion_tokens: 100,
            total_tokens: 1100,
            requests: 1,
          },
          agent_lorebook: {
            prompt_tokens: 800,
            completion_tokens: 200,
            total_tokens: 1000,
            requests: 1,
          },
        },
      },
    }]);

    await store._consumeStream(session, assistantMsg, false, reader, 0, {
      agentId: 'agent_director',
      contextKey: 'global',
      streamEpoch: 1,
    });

    expect(session.contextTokenCount).toBe(2100);
    expect(session.contextTokenUsage).toMatchObject({
      promptTokens: 1800,
      completionTokens: 300,
      totalTokens: 2100,
      requests: 2,
    });
    expect(session.contextTokenUsage?.byAgent.agent_lorebook.totalTokens).toBe(1000);
    expect(assistantMsg.metadata.llm_usage.total_tokens).toBe(2100);
  });

  it('消费上下文压缩事件并将同一动画段更新为完成态', async () => {
    const store = useChatStore();
    const session = store.primarySession;
    session.sending = true;
    session.backgroundTaskStatus = 'running';
    session.streamEpoch = 1;
    const assistantMsg: any = {
      clientId: 'assistant-compaction',
      role: 'assistant',
      content: '',
      reasoning: '',
      tool_traces: [],
      segments: [],
      timestamp: 1,
    };
    const reader = readerFromEvents([
      {
        event: 'context_compaction_started',
        seq: 1,
        original_tokens: 9000,
        retained_messages: 4,
        model: 'offline-model',
      },
      {
        event: 'context_compaction_finished',
        seq: 2,
        original_tokens: 9000,
        compacted_tokens: 2200,
        retained_messages: 4,
        model: 'offline-model',
      },
      { event: 'task_done', seq: 3, status: 'completed' },
    ]);

    await store._consumeStream(session, assistantMsg, false, reader, 0, {
      agentId: 'agent_director',
      contextKey: 'global',
      streamEpoch: 1,
    });

    expect(assistantMsg.segments).toHaveLength(1);
    expect(assistantMsg.segments[0]).toMatchObject({
      type: 'context_compaction',
      status: 'finished',
      original_tokens: 9000,
      compacted_tokens: 2200,
    });
  });

  it('导演启动自动写作时先同步注册 running 状态再通知覆盖层', async () => {
    const store = useChatStore();
    const session = store.primarySession;
    session.sending = true;
    session.backgroundTaskStatus = 'running';
    session.streamEpoch = 1;
    const assistantMsg: any = {
      clientId: 'assistant-auto-write',
      role: 'assistant',
      content: '',
      reasoning: '',
      tool_traces: [],
      segments: [],
      timestamp: 1,
    };
    let statusWhenNotified = '';
    const onStarted = () => {
      statusWhenNotified = useDirectorAutoWriteStore().tasks['测试项目']?.snapshot.status || '';
    };
    bus.on('director-auto-write-started', onStarted);
    const emitSpy = vi.spyOn(bus, 'emit');

    const reader = readerFromEvents([
      {
        event: 'director_auto_write_started',
        seq: 1,
        project_name: '测试项目',
        start_chapter_index: 0,
        start_scene_index: 0,
        mode: 'continuous_write',
        export_format: 'arc',
        total_chapters: 3,
        total_scenes: 12,
      },
      { event: 'task_done', seq: 2, status: 'completed' },
    ]);

    await store._consumeStream(session, assistantMsg, false, reader, 0, {
      agentId: 'agent_director',
      contextKey: 'global',
      streamEpoch: 1,
    });

    const task = useDirectorAutoWriteStore().tasks['测试项目'];
    expect(task?.snapshot.status).toBe('running');
    expect(task?.fromDirector).toBe(true);
    expect(statusWhenNotified).toBe('running');
    expect(emitSpy).toHaveBeenCalledWith('director-auto-write-started', expect.objectContaining({
      project_name: '测试项目',
    }));
    bus.off('director-auto-write-started', onStarted);
    vi.clearAllTimers();
  });

  it('对短上下文模型错误显示本地化提示且不进入模型重试态', async () => {
    const store = useChatStore();
    const session = store.primarySession;
    session.sending = true;
    session.backgroundTaskStatus = 'running';
    session.streamEpoch = 1;
    const assistantMsg: any = {
      clientId: 'assistant-context-error',
      role: 'assistant',
      content: '',
      reasoning: '',
      tool_traces: [],
      segments: [],
      timestamp: 1,
    };
    const reader = readerFromEvents([
      {
        event: 'error',
        seq: 1,
        code: 'context_window_incompatible',
        message: 'backend fallback',
        retryable: false,
      },
      { event: 'task_done', seq: 2, status: 'error', error: 'backend fallback' },
    ]);

    await store._consumeStream(session, assistantMsg, false, reader, 0, {
      agentId: 'agent_director',
      contextKey: 'global',
      streamEpoch: 1,
    });

    expect(session.lastError).not.toBe('backend fallback');
    expect(String(session.lastError || '').length).toBeGreaterThan(20);
    expect(session.retryAttempt).toBeNull();
    expect(session.retryMode).toBeNull();
  });

  it('观察流断开但后台任务仍运行时保留运行态', async () => {
    vi.mocked(getChatTaskStatus).mockResolvedValueOnce({
      hasTask: true,
      status: 'running',
      agentId: 'agent_director',
      contextKey: 'global',
    });

    const store = useChatStore();
    const session = store.primarySession;
    session.sending = false;
    session.backgroundTaskStatus = null;

    const stillRunning = await store._isChatTaskStillRunning('agent_director', 'global');
    if (stillRunning) {
      session.backgroundTaskStatus = 'running';
      session.sending = true;
    }

    expect(stillRunning).toBe(true);
    expect(session.sending).toBe(true);
    expect(session.backgroundTaskStatus).toBe('running');
  });

  it('观察流重连发现 cancelled 终态时清锁并刷新权威历史', async () => {
    vi.mocked(getChatTaskStatus).mockResolvedValueOnce({
      hasTask: true,
      status: 'cancelled',
      agentId: 'agent_director',
      contextKey: 'global',
    });
    const store = useChatStore();
    const session = store.primarySession;
    session.sending = true;
    session.backgroundTaskStatus = 'running';
    session.streamEpoch = 1;

    await expect(
      store._recoverChatStreamObserver(session, 'agent_director', 'global', 4, 1),
    ).resolves.toBe(true);

    expect(session.sending).toBe(false);
    expect(session.backgroundTaskStatus).toBeNull();
    expect(getChatHistory).toHaveBeenCalledWith('测试项目', 'agent_director', 'global', 80);
  });

  it('任务状态查询失败时返回未知态而不是误判为结束', async () => {
    vi.mocked(getChatTaskStatus).mockRejectedValueOnce(new TypeError('Failed to fetch'));

    const store = useChatStore();

    await expect(
      store._getChatTaskAuthorityState('agent_director', 'global'),
    ).resolves.toBe('unknown');
  });

  it('只有权威终态才能解除发送锁定', () => {
    const store = useChatStore();
    const session = store.primarySession;

    expect(store._applyChatTaskAuthorityState(session, 'unknown')).toBe(true);
    expect(session.sending).toBe(true);
    expect(session.backgroundTaskStatus).toBe('running');

    expect(store._applyChatTaskAuthorityState(session, 'terminal')).toBe(false);
    expect(session.sending).toBe(false);
    expect(session.backgroundTaskStatus).toBeNull();
  });

  it('没有较早上下文可压缩时保留窗口统计且不刷新历史', async () => {
    vi.mocked(compactChatContext).mockResolvedValueOnce({
      success: true,
      compacted: false,
    });
    const store = useChatStore();
    const session = store.primarySession;
    session.contextWindowStats = { inputTokens: 1200, outputTokens: 80 } as any;
    const refreshSpy = vi.spyOn(store, 'refreshSessionHistory');
    const toasts: any[] = [];
    const onToast = (payload: any) => toasts.push(payload);
    bus.on('toast', onToast);

    await store.compactSessionContext(session.id);

    bus.off('toast', onToast);
    expect(session.contextWindowStats).toEqual({ inputTokens: 1200, outputTokens: 80 });
    expect(refreshSpy).not.toHaveBeenCalled();
    expect(toasts.at(-1)?.type).toBe('info');
  });

  it('状态查询短暂断网时保持运行态并继续按游标恢复观察流', async () => {
    vi.mocked(getChatTaskStatus)
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({
        hasTask: true,
        status: 'running',
        agentId: 'agent_director',
        contextKey: 'global',
        lastSeq: 9,
      });

    const store = useChatStore();
    const session = store.primarySession;
    session.streamEpoch = 1;

    const reconnectSpy = vi
      .spyOn(store, '_reconnectTaskStream')
      .mockImplementationOnce(async () => undefined);

    const recovered = store._resumeChatTaskAfterTransportLoss(
      session,
      'agent_director',
      'global',
      7,
      1,
    );

    await Promise.resolve();
    expect(session.sending).toBe(true);
    expect(session.backgroundTaskStatus).toBe('running');
    expect(session.retryMode).toBe('transport');
    expect(session.retryAttempt).toBe(1);

    await vi.advanceTimersByTimeAsync(800);
    await expect(recovered).resolves.toBe(true);

    expect(reconnectSpy).toHaveBeenCalledWith(
      session,
      'agent_director',
      'global',
      7,
      {
        retryIndex: 0,
        maxTransportRetries: 3,
      },
    );
  });
});
