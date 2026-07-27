import { onUnmounted, ref } from 'vue'

const query = window.matchMedia('(max-width: 768px)')

export function useIsMobile() {
  const isMobile = ref(query.matches)
  const handler = (event) => { isMobile.value = event.matches }
  query.addEventListener('change', handler)
  onUnmounted(() => query.removeEventListener('change', handler))
  return { isMobile }
}
