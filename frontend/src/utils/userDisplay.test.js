import { describe, expect, it } from "vitest";
import { userDisplayName, userInitial } from "./userDisplay";

describe("userDisplayName", () => {
  it("prefers a stored user.name", () => {
    expect(userDisplayName({ name: "Tanishka", email: "rudratanishka1@gmail.com" })).toBe("Tanishka");
  });

  it("falls back to the email prefix when name is absent", () => {
    expect(userDisplayName({ email: "rudratanishka1@gmail.com" })).toBe("rudratanishka1");
    expect(userDisplayName({ name: "   ", email: "person@example.test" })).toBe("person");
  });

  it("uses Career seeker only as the final generic fallback", () => {
    expect(userDisplayName({})).toBe("Career seeker");
    expect(userDisplayName(null)).toBe("Career seeker");
  });

  it("initial uses the same display-name source", () => {
    expect(userInitial({ name: "Tanishka" })).toBe("T");
    expect(userInitial({ email: "rudratanishka1@gmail.com" })).toBe("R");
    expect(userInitial({})).toBe("C");
  });
});
