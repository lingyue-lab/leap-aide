/**
 * fontMonitor — Debug utility to track font loading issues.
 * Logs when fonts fail to load with detailed error info.
 */

let _initialized = false;

export function initFontMonitor(): void {
  if (_initialized) return;
  _initialized = true;

  // Monitor font loading via document.fonts API
  if (document.fonts) {
    document.fonts.addEventListener('loadingdone', (e) => {
      console.log(`[FontMonitor] loadingdone: ${e.fontfaces.length} font(s) loaded`);
      for (const font of e.fontfaces) {
        console.log(`[FontMonitor]   - ${font.family} ${font.weight} ${font.style}`);
      }
    });

    document.fonts.addEventListener('loadingerror', (e) => {
      console.error(`[FontMonitor] loadingerror: ${e.fontfaces.length} font(s) failed`);
      for (const font of e.fontfaces) {
        console.error(`[FontMonitor]   - ${font.family} ${font.weight} ${font.style}`);
        console.error(`[FontMonitor]     status: ${font.status}`);
      }
    });
  }

  // Monitor resource loading errors via PerformanceObserver
  if (typeof PerformanceObserver !== 'undefined') {
    try {
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (entry.name.includes('.woff') || entry.name.includes('.woff2') || entry.name.includes('material-symbols')) {
            const perfEntry = entry as PerformanceResourceTiming;
            const isError = perfEntry.transferSize === 0 || perfEntry.responseStatus === 0;
            const level = isError ? 'error' : 'warn';
            console[level](`[FontMonitor] Resource: ${entry.name}`);
            console[level](`[FontMonitor]   duration: ${entry.duration.toFixed(2)}ms`);
            console[level](`[FontMonitor]   transferSize: ${perfEntry.transferSize ?? 'N/A'}`);
            console[level](`[FontMonitor]   responseStatus: ${perfEntry.responseStatus ?? 'N/A'}`);
            console[level](`[FontMonitor]   initiatorType: ${perfEntry.initiatorType}`);
            console[level](`[FontMonitor]   fetchStart: ${perfEntry.fetchStart.toFixed(2)}ms`);
            console[level](`[FontMonitor]   responseEnd: ${perfEntry.responseEnd.toFixed(2)}ms`);
            if (isError) {
              console.error(`[FontMonitor]   >>> CONNECTION FAILED - no response received`);
            }
          }
        }
      });
      observer.observe({ entryTypes: ['resource'] });
    } catch {
      // PerformanceObserver not supported
    }
  }
}

/**
 * Check if Material Symbols font is loaded and log status.
 */
export function checkMaterialSymbolsFont(): void {
  if (!document.fonts) return;

  const check = async () => {
    try {
      const loaded = await document.fonts.check('24px Material Symbols Rounded');
      console.log(`[FontMonitor] Material Symbols Rounded loaded: ${loaded}`);

      // List all loaded fonts
      const fonts: string[] = [];
      document.fonts.forEach((font) => {
        if (font.family.includes('Material')) {
          fonts.push(`${font.family} ${font.weight} ${font.style} [${font.status}]`);
        }
      });
      if (fonts.length > 0) {
        console.log(`[FontMonitor] Material fonts:`, fonts);
      }
    } catch (e) {
      console.error(`[FontMonitor] check error:`, e);
    }
  };

  // Check immediately and after a delay
  check();
  setTimeout(check, 2000);
  setTimeout(check, 5000);
}
