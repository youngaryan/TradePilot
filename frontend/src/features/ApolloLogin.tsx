/**
 * Compatibility shim.
 *
 * The primary sign-in surface now lives in `features/auth/SignIn` and is shared
 * by every entry point. This module is kept so existing imports of
 * `ApolloLogin` keep resolving.
 */
export { SignIn as ApolloLogin, SignIn, default } from "./auth/SignIn";
