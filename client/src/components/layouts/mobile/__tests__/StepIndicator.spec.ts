import { mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import StepIndicator from '../StepIndicator.vue';
import { i18n } from '@/i18n';

const steps = [
  { id: 'home', label: '首页' },
  { id: 'muse', label: '灵感' },
  { id: 'lorebook', label: '世界' },
];

function mountIndicator(currentStep = 1) {
  return mount(StepIndicator, {
    props: { steps, currentStep },
    attachTo: document.body,
    global: {
      plugins: [i18n],
    },
  });
}

describe('StepIndicator 常驻图标竖栏', () => {
  afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = '';
  });

  it('渲染全部步骤并高亮当前步骤', () => {
    const wrapper = mountIndicator(1);
    const items = wrapper.findAll('.nav-item');

    expect(items).toHaveLength(3);
    expect(items[1].classes()).toContain('is-active');
    expect(items[1].attributes('aria-current')).toBe('step');
    wrapper.unmount();
  });

  it('点击其他步骤滚动到对应位置', async () => {
    const target = document.createElement('section');
    target.id = 'step-2';
    target.scrollIntoView = vi.fn();
    document.body.appendChild(target);

    const wrapper = mountIndicator(1);
    await wrapper.findAll('.nav-item')[2].trigger('click');

    expect(target.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' });
    wrapper.unmount();
  });

  it('点击当前步骤不触发滚动', async () => {
    const current = document.createElement('section');
    current.id = 'step-1';
    current.scrollIntoView = vi.fn();
    document.body.appendChild(current);

    const wrapper = mountIndicator(1);
    await wrapper.findAll('.nav-item')[1].trigger('click');

    expect(current.scrollIntoView).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('主滚动容器滚动时淡出（暂停交互），停止后恢复', async () => {
    vi.useFakeTimers();
    const container = document.createElement('main');
    container.className = 'flow-container';
    document.body.appendChild(container);

    const wrapper = mountIndicator(0);
    container.dispatchEvent(new Event('scroll'));
    await wrapper.vm.$nextTick();
    expect(wrapper.find('.flow-nav').classes()).toContain('is-dimmed');

    await vi.advanceTimersByTimeAsync(700);
    expect(wrapper.find('.flow-nav').classes()).not.toContain('is-dimmed');
    wrapper.unmount();
  });
});
