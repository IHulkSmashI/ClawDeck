const R = (window as any).React;
export const Fragment = R.Fragment;
export const jsx = (type: any, props: any, key?: any) => R.createElement(type, key === undefined ? props : { ...props, key });
export const jsxs = jsx;
