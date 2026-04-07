/**
 * Tests for cron expression parser.
 */
import { describe, expect, it } from "vitest";
import { cronMatches, describeCron, nextCronOccurrence, parseCron } from "./cron-parser.js";

describe("parseCron", () => {
  it("should parse a simple expression", () => {
    const cron = parseCron("0 7 * * 1-5");
    expect(cron.minutes.has(0)).toBe(true);
    expect(cron.hours.has(7)).toBe(true);
    expect(cron.daysOfWeek.has(1)).toBe(true);
    expect(cron.daysOfWeek.has(5)).toBe(true);
    expect(cron.daysOfWeek.has(0)).toBe(false); // Sunday
    expect(cron.daysOfWeek.has(6)).toBe(false); // Saturday
  });

  it("should parse step values", () => {
    const cron = parseCron("*/15 * * * *");
    expect([...cron.minutes].sort((a, b) => a - b)).toEqual([0, 15, 30, 45]);
  });

  it("should parse lists", () => {
    const cron = parseCron("0,30 9,15 * * *");
    expect(cron.minutes.has(0)).toBe(true);
    expect(cron.minutes.has(30)).toBe(true);
    expect(cron.hours.has(9)).toBe(true);
    expect(cron.hours.has(15)).toBe(true);
  });

  it("should parse range with step", () => {
    const cron = parseCron("0 9-15/2 * * *");
    expect([...cron.hours].sort((a, b) => a - b)).toEqual([9, 11, 13, 15]);
  });

  it("should throw on invalid expression", () => {
    expect(() => parseCron("0 7 *")).toThrow("expected 5 fields");
    expect(() => parseCron("")).toThrow("expected 5 fields");
  });

  it("should throw on invalid field values", () => {
    expect(() => parseCron("abc 7 * * *")).toThrow("Invalid");
  });
});

describe("cronMatches", () => {
  it("should match weekday 07:00", () => {
    const cron = parseCron("0 7 * * 1-5");
    // Monday 2026-04-06 07:00
    const monday7am = new Date(2026, 3, 6, 7, 0);
    expect(cronMatches(cron, monday7am)).toBe(true);

    // Sunday 2026-04-05 07:00
    const sunday7am = new Date(2026, 3, 5, 7, 0);
    expect(cronMatches(cron, sunday7am)).toBe(false);

    // Monday 08:00
    const monday8am = new Date(2026, 3, 6, 8, 0);
    expect(cronMatches(cron, monday8am)).toBe(false);
  });

  it("should match Friday 17:00", () => {
    const cron = parseCron("0 17 * * 5");
    // Friday 2026-04-10 17:00
    const friday5pm = new Date(2026, 3, 10, 17, 0);
    expect(cronMatches(cron, friday5pm)).toBe(true);

    // Thursday 2026-04-09 17:00
    const thursday5pm = new Date(2026, 3, 9, 17, 0);
    expect(cronMatches(cron, thursday5pm)).toBe(false);
  });

  it("should match every 30 minutes", () => {
    const cron = parseCron("*/30 * * * *");
    expect(cronMatches(cron, new Date(2026, 0, 1, 10, 0))).toBe(true);
    expect(cronMatches(cron, new Date(2026, 0, 1, 10, 30))).toBe(true);
    expect(cronMatches(cron, new Date(2026, 0, 1, 10, 15))).toBe(false);
  });
});

describe("nextCronOccurrence", () => {
  it("should find next weekday 07:00", () => {
    const cron = parseCron("0 7 * * 1-5");
    // From Monday 2026-04-06 06:30 → should be Monday 07:00
    const from = new Date(2026, 3, 6, 6, 30);
    const next = nextCronOccurrence(cron, from);
    expect(next).not.toBeNull();
    expect(next!.getHours()).toBe(7);
    expect(next!.getMinutes()).toBe(0);
    expect(next!.getDay()).toBeGreaterThanOrEqual(1);
    expect(next!.getDay()).toBeLessThanOrEqual(5);
  });

  it("should skip to next week if past Friday", () => {
    const cron = parseCron("0 7 * * 1-5");
    // From Friday 2026-04-10 08:00 → should be next Monday 07:00
    const from = new Date(2026, 3, 10, 8, 0);
    const next = nextCronOccurrence(cron, from);
    expect(next).not.toBeNull();
    expect(next!.getDay()).toBe(1); // Monday
    expect(next!.getHours()).toBe(7);
    expect(next!.getDate()).toBe(13); // April 13, 2026
  });

  it("should find next occurrence for every-15-min cron", () => {
    const cron = parseCron("*/15 * * * *");
    const from = new Date(2026, 3, 6, 10, 10);
    const next = nextCronOccurrence(cron, from);
    expect(next).not.toBeNull();
    expect(next!.getMinutes()).toBe(15);
  });
});

describe("describeCron", () => {
  it("should describe weekday morning", () => {
    expect(describeCron("0 7 * * 1-5")).toContain("周一至周五");
    expect(describeCron("0 7 * * 1-5")).toContain("7时");
  });

  it("should describe Friday afternoon", () => {
    expect(describeCron("0 17 * * 5")).toContain("周五");
    expect(describeCron("0 17 * * 5")).toContain("17时");
  });

  it("should describe every-N-minutes", () => {
    expect(describeCron("*/30 * * * *")).toContain("30分钟");
  });
});
