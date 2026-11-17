<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number }>(), { size: 20 })
const paths: Record<string, string> = {
  plus: 'M12 5v14M5 12h14', arrow: 'M5 12h14m-5-5 5 5-5 5', back: 'M19 12H5m5-5-5 5 5 5',
  image: 'M4 4h16v16H4zM4 16l5-5 4 4 3-3 4 4M15 8h.01',
  spark: 'm12 3 2.6 6.4L21 12l-6.4 2.6L12 21l-2.6-6.4L3 12l6.4-2.6L12 3z',
  check: 'm5 12 4 4L19 6', download: 'M12 3v12m-5-5 5 5 5-5M4 15v6h16v-6',
  clock: 'M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  file: 'M14 3H5v18h14V8l-5-5zM14 3v6h5M8 13h8M8 17h6',
  grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  upload: 'M12 17V3m-5 5 5-5 5 5M4 16v5h16v-5',
  refresh: 'M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1',
  close: 'm6 6 12 12M6 18 18 6', warning: 'm12 3 10 18H2L12 3zM12 9v5m0 3h.01',
}
</script>
<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="paths[name] || paths.spark" /></svg>
</template>
