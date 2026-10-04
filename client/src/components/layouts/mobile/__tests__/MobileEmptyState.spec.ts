import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import { Sparkles } from '@lucide/vue';
import MobileEmptyState from '../MobileEmptyState.vue';

describe('MobileEmptyState 统一空状态', () => {
  it('渲染标题与提示，并暴露 status 语义', () => {
    const wrapper = mount(MobileEmptyState, {
      props: { icon: Sparkles, title: '暂无内容', hint: '先去创建一个' },
    });
    expect(wrapper.attributes('role')).toBe('status');
    expect(wrapper.text()).toContain('暂无内容');
    expect(wrapper.text()).toContain('先去创建一个');
  });

  it('没有 hint 与插槽时不渲染多余节点', () => {
    const wrapper = mount(MobileEmptyState, { props: { icon: Sparkles, title: 'x' } });
    expect(wrapper.find('.m-empty-hint').exists()).toBe(false);
    expect(wrapper.find('.m-empty-extra').exists()).toBe(false);
  });

  it('fill / compact 与默认插槽生效', () => {
    const wrapper = mount(MobileEmptyState, {
      props: { icon: Sparkles, title: 'x', fill: true, compact: true },
      slots: { default: '<button class="cta">去创建</button>' },
    });
    expect(wrapper.classes()).toEqual(expect.arrayContaining(['is-fill', 'is-compact']));
    expect(wrapper.find('.m-empty-extra .cta').exists()).toBe(true);
  });
});
