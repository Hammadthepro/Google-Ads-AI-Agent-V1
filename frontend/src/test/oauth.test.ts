import { checkAuthorizationUrl } from "../lib/oauth";

const good = "https://accounts.google.com/o/oauth2/v2/auth?client_id=609-abc.apps.googleusercontent.com&redirect_uri=https%3A%2F%2Fx-5173.app.github.dev%2Fapi%2Fv1%2Fintegrations%2Fgoogle%2Fcallback&response_type=code&scope=email+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fadwords+openid&access_type=offline&prompt=consent&state=abc123&code_challenge=zzz&code_challenge_method=S256";

describe("checkAuthorizationUrl", () => {
  it("accepts the server's URL and returns it byte for byte (no re-encoding, no dropped parameters)", () => {
    const r = checkAuthorizationUrl(good);
    expect(r).toEqual({ ok: true, url: good });
  });
  it("refuses a request without response_type instead of sending it to Google", () => {
    const r = checkAuthorizationUrl(good.replace("&response_type=code", ""));
    expect(r.ok).toBe(false);
    expect((r as any).reason).toMatch(/response_type/);
  });
  it("refuses wrong host, wrong response_type, or a truncated query", () => {
    expect(checkAuthorizationUrl(good.replace("accounts.google.com", "evil.example")).ok).toBe(false);
    expect(checkAuthorizationUrl(good.replace("response_type=code", "response_type=token")).ok).toBe(false);
    expect(checkAuthorizationUrl(good.split("&")[0]).ok).toBe(false); // what an unquoted `&` in a shell would leave behind
    expect(checkAuthorizationUrl(undefined).ok).toBe(false);
  });
});
