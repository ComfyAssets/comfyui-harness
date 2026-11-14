// The specs live outside tools/testing, where @playwright/test is installed;
// import it from there by path so the runner and the specs share one instance.
export * from "../../tools/testing/node_modules/@playwright/test/index.mjs";
