import {
  AuthenticationDetails,
  CognitoUser,
  CognitoUserPool,
  type CognitoUserSession,
} from "amazon-cognito-identity-js";
import type { Auth, SignInResult } from "./types";

export function createAuth(userPoolId: string, clientId: string): Auth & { getToken(): Promise<string> } {
  const pool = new CognitoUserPool({ UserPoolId: userPoolId, ClientId: clientId });
  let user: CognitoUser | null = null;
  let pendingAttrs: Record<string, string> = {};

  const sessionOf = (u: CognitoUser) =>
    new Promise<CognitoUserSession>((resolve, reject) =>
      u.getSession((err: Error | null, s: CognitoUserSession | null) => (s ? resolve(s) : reject(err))),
    );

  return {
    email: () => user?.getUsername() ?? null,

    async restore() {
      const cached = pool.getCurrentUser();
      if (!cached) return false;
      try {
        await sessionOf(cached); // refreshes the token if needed
        user = cached;
        return true;
      } catch {
        return false;
      }
    },

    signIn(email, password) {
      user = new CognitoUser({ Username: email, Pool: pool });
      return new Promise<SignInResult>((resolve, reject) => {
        user!.authenticateUser(new AuthenticationDetails({ Username: email, Password: password }), {
          onSuccess: () => resolve({ status: "ok" }),
          onFailure: reject,
          // Admin-created users must choose their own password on first sign-in.
          newPasswordRequired: (attrs: Record<string, string>) => {
            delete attrs.email_verified;
            delete attrs.email;
            pendingAttrs = attrs;
            resolve({ status: "new_password_required" });
          },
        });
      });
    },

    completeNewPassword(newPassword) {
      return new Promise<void>((resolve, reject) =>
        user!.completeNewPasswordChallenge(newPassword, pendingAttrs, {
          onSuccess: () => resolve(),
          onFailure: reject,
        }),
      );
    },

    signOut() {
      (user ?? pool.getCurrentUser())?.signOut();
      user = null;
    },

    async getToken() {
      const u = user ?? pool.getCurrentUser();
      if (!u) throw new Error("Not signed in");
      return (await sessionOf(u)).getIdToken().getJwtToken();
    },
  };
}
