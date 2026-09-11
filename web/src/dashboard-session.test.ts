import { afterEach, expect, it, vi } from "vitest";
import { api, AuthenticationRequiredError } from "./api";
import { connectDashboardSession } from "./dashboard-session";

afterEach(() => vi.unstubAllGlobals());

it("does not save a rejected token", async () => {
  const setItem = vi.fn();
  vi.stubGlobal("window", {sessionStorage: {setItem}});
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ok:false}));
  await expect(connectDashboardSession("test-token", "test-org")).rejects.toThrow("Connection rejected");
  expect(setItem).not.toHaveBeenCalled();
});

it("uses the current server and tab-scoped storage after acceptance", async () => {
  const setItem = vi.fn();
  const request = vi.fn().mockResolvedValue({ok:true});
  vi.stubGlobal("window", {sessionStorage: {setItem}});
  vi.stubGlobal("fetch", request);
  await connectDashboardSession("test-token", "test-org");
  expect(request).toHaveBeenCalledWith("/api/v1/meta", {headers: {
    Authorization:"Bearer test-token", "X-Witdem-Organization-ID":"test-org",
  }});
  expect(setItem).toHaveBeenCalledWith("witdem:access-token", "test-token");
});

it("turns 401 into an authentication event instead of a transient error", async () => {
  const dispatchEvent = vi.fn();
  vi.stubGlobal("window", {dispatchEvent, sessionStorage:{getItem:()=>null}});
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({status:401,ok:false}));
  await expect(api.meta()).rejects.toBeInstanceOf(AuthenticationRequiredError);
  expect(dispatchEvent.mock.calls[0][0].type).toBe("witdem:authentication-required");
});
