import { describe, expect, it } from 'vitest';
import { adaptToolDetails } from '../toolDetails';

describe('工具详情展示适配', () => {
  it('只展示委派工具允许的输入字段，并保留返回结果', () => {
    const details = adaptToolDetails('delegate_task', {
      tool_input: {
        target_agent: 'agent_muse',
        task_description: '寻找灵感',
        api_key: '不能展示',
      },
      tool_result: '__DELEGATE__:完成',
    });

    expect(details.expandable).toBe(true);
    expect(details.sections).toHaveLength(2);
    expect(details.sections[0].entries.map(item => item.key)).toEqual([
      'target_agent',
      'task_description',
    ]);
    expect(details.sections[0].entries.some(item => item.text.includes('不能展示'))).toBe(false);
    expect(details.sections[1].entries[0].text).toContain('__DELEGATE__');
  });

  it('隐藏工具发生失败时也允许展开错误原因', () => {
    const details = adaptToolDetails('list_files', {
      tool_input: { path: 'stories', api_key: '不能展示' },
      tool_error: '参数校验失败',
    });

    expect(details.expandable).toBe(true);
    expect(details.sections.map(section => section.key)).toEqual(['error']);
    expect(details.sections[0].entries[0].text).toBe('参数校验失败');
  });

  it('局部替换和联网搜索只暴露适合用户阅读的字段', () => {
    const patch = adaptToolDetails('patch_script', {
      tool_input: {
        search_text: '旧内容',
        replace_text: '新内容',
        export_format: 'arc',
      },
      tool_result: '已完成局部替换',
    });
    const search = adaptToolDetails('web_search', {
      tool_input: {
        query: '资料',
        provider: 'exa',
        api_key: '不能展示',
      },
    });

    expect(patch.sections[0].entries.map(item => item.key)).toEqual(['search_text', 'replace_text']);
    expect(search.sections[0].entries.map(item => item.key)).toEqual(['provider', 'query']);
  });

  it('批量故事工具允许展开操作清单和结果', () => {
    const details = adaptToolDetails('batch_rename_chapters', {
      tool_input: {
        renames: [{ path: '一 · 开端', new_name: '序幕' }],
        internal_state: '不能展示',
      },
      tool_result: '已同步 stories_order.json',
    });

    expect(details.expandable).toBe(true);
    expect(details.sections.map(section => section.key)).toEqual(['input', 'result']);
    expect(details.sections[0].entries.map(item => item.key)).toEqual(['renames']);
    expect(details.sections[0].entries[0].text).toContain('一 · 开端');
    expect(details.sections[0].entries[0].text).not.toContain('不能展示');
  });

  it('滑窗读窗只展示指针，检索展示搜了什么', () => {
    const read = adaptToolDetails('read_longread_window', {
      tool_input: { source_id: 'abc123', chunk_index: 2, secret: '不能展示' },
      tool_result: '窗口正文不应展示',
    });
    expect(read.expandable).toBe(true);
    expect(read.sections.map(section => section.key)).toEqual(['input']);
    expect(read.sections[0].entries.map(item => item.key)).toEqual(['source_id', 'chunk_index']);

    const search = adaptToolDetails('search_project', {
      tool_input: { pattern: '玉佩', scope: ['attachment'], max_results: 20, secret: '不能展示' },
      tool_result: '命中正文不应展示',
    });
    expect(search.expandable).toBe(true);
    expect(search.sections.map(section => section.key)).toEqual(['input']);
    expect(search.sections[0].entries.map(item => item.key)).toEqual(['pattern', 'scope', 'max_results']);
  });
});
