// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { renderMarkdown } from './markdown.js'

describe('renderMarkdown', () => {
  it('keeps ordinary markdown', () => {
    expect(renderMarkdown('**安全**')).toContain('<strong>安全</strong>')
  })

  it('removes executable model output', () => {
    const html = renderMarkdown('<img src=x onerror="alert(1)"><script>alert(2)</script>')
    expect(html).toContain('<img src="x">')
    expect(html).not.toContain('onerror')
    expect(html).not.toContain('<script')
  })
})
