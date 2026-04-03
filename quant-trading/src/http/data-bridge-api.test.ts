import { describe, it, expect, beforeEach } from "vitest";
import {
  handleDataBridgePostRequest,
  handleDataBridgePollRequest,
  handleDataBridgePostResponse,
  handleDataBridgePollResponse,
} from "./data-bridge-api.js";

describe("data-bridge-api", () => {
  // Each test gets a fresh set of requests by using unique codes

  it("handles concurrent requests independently", () => {
    // Post request 1
    const res1 = handleDataBridgePostRequest({
      codes: ["600519.SH"],
      period: "1d",
      count: 10,
    });
    expect(res1.status).toBe("ok");
    const id1 = res1.requestId as string;

    // Post request 2
    const res2 = handleDataBridgePostRequest({
      codes: ["000001.SZ"],
      period: "1d",
      count: 10,
    });
    expect(res2.status).toBe("ok");
    const id2 = res2.requestId as string;

    // Both request IDs should be different
    expect(id1).not.toBe(id2);

    // Poll should return the first pending request
    const poll = handleDataBridgePollRequest();
    expect(poll).not.toBeNull();
    expect([id1, id2]).toContain(poll!.requestId);
  });

  it("polls response by requestId", () => {
    const res = handleDataBridgePostRequest({
      codes: ["601318.SH"],
      period: "1d",
      count: 5,
    });
    const reqId = res.requestId as string;

    // Submit response for this specific request
    const postRes = handleDataBridgePostResponse({
      requestId: reqId,
      data: {
        "601318": [{ date: "20250101", open: 50, high: 55, low: 49, close: 52, volume: 100000 }],
      },
    });
    expect(postRes.status).toBe("ok");

    // Should find the response by its requestId
    const poll = handleDataBridgePollResponse(reqId);
    expect(poll).not.toBeNull();
    expect(poll!.requestId).toBe(reqId);
    expect(Object.keys(poll!.data)).toContain("601318");

    // A different requestId should return null
    expect(handleDataBridgePollResponse("nonexistent-id")).toBeNull();
  });

  it("rejects invalid body", () => {
    expect(handleDataBridgePostRequest(null)).toHaveProperty("status", "error");
    expect(handleDataBridgePostRequest({ codes: [] })).toHaveProperty("status", "error");
    expect(handleDataBridgePostResponse(null)).toHaveProperty("status", "error");
    expect(handleDataBridgePostResponse({ requestId: "x" })).toHaveProperty("status", "error");
  });

  it("handles error responses from bridge", () => {
    const res = handleDataBridgePostRequest({
      codes: ["999999.SH"],
      period: "1d",
    });
    const reqId = res.requestId as string;

    const errRes = handleDataBridgePostResponse({
      requestId: reqId,
      error: "xtdata connection failed",
    });
    expect(errRes.status).toBe("ok");
    expect(errRes.error).toBe("xtdata connection failed");
  });
});
