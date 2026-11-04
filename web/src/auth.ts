// Keycloak login with authorization code + PKCE (client "studio-web"). The SPA keeps the
// access token in memory (oidc-client-ts session storage) and sends it as a bearer token.
import { User, UserManager, WebStorageStateStore } from "oidc-client-ts";

const authority = import.meta.env.VITE_OIDC_AUTHORITY ?? "http://localhost:8080/realms/studio";

export const userManager = new UserManager({
  authority,
  client_id: import.meta.env.VITE_OIDC_CLIENT_ID ?? "studio-web",
  redirect_uri: `${window.location.origin}/callback`,
  post_logout_redirect_uri: window.location.origin,
  response_type: "code",
  scope: "openid profile email",
  automaticSilentRenew: true,
  userStore: new WebStorageStateStore({ store: window.sessionStorage }),
});

export async function currentUser(): Promise<User | null> {
  const user = await userManager.getUser();
  return user && !user.expired ? user : null;
}

export async function accessToken(): Promise<string> {
  const user = await currentUser();
  if (!user) {
    await userManager.signinRedirect({ state: window.location.pathname + window.location.search });
    throw new Error("redirecting to login");
  }
  return user.access_token;
}
