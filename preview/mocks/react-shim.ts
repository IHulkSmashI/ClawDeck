// The preview loads React from cdnjs (UMD global); these shims point the bundle's imports at it.
const R = (window as any).React;
export default R;
export const { useState, useEffect, useRef, useMemo, useCallback, Fragment, createElement } = R;
