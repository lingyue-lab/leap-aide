// Minimal typing for noVNC's RFB client. The package ships plain ESM with
// no bundled .d.ts; this declaration covers only the surface we use.
// Note: the package's "exports" map only exposes the root specifier, so
// the import must be `@novnc/novnc` (which resolves to core/rfb.js).
declare module '@novnc/novnc' {
  export default class RFB {
    constructor(target: HTMLElement, url: string, options?: Record<string, unknown>);
    addEventListener(type: string, listener: (e: Event) => void): void;
    removeEventListener(type: string, listener: (e: Event) => void): void;
    disconnect(): void;
    focus(): void;
    background: string;
    scaleViewport: boolean;
    resizeSession: boolean;
    clipViewport: boolean;
    viewOnly: boolean;
    credentials: Record<string, string>;
  }
}
