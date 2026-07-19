// TypeScript 7's native compiler no longer implicitly types untyped
// side-effect imports of non-code assets (e.g. `import './globals.css'`),
// which previously compiled under TS 5. Declare CSS as an ambient module so
// side-effect stylesheet imports typecheck. See issue #305 (framework upgrade).
declare module '*.css';
