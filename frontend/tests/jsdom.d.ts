declare module 'jsdom' {
  export class JSDOM {
    constructor(html?: string, options?: { url?: string })
    window: { localStorage: Storage; sessionStorage: Storage }
  }
}
