<script setup lang="ts">
import { useMobile } from '../../composables/useMobile';
import Desktop from './HomeDesktop.vue';
import Mobile from './HomeMobile.vue';

const { isMobile } = useMobile();
</script>

<template>
  <Mobile v-if="isMobile" />
  <Desktop v-else />
</template>
