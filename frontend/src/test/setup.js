import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

class ObserverMock { observe() {} unobserve() {} disconnect() {} }

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query) => ({
    matches: false, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  })),
});
globalThis.ResizeObserver = ObserverMock;
globalThis.IntersectionObserver = ObserverMock;
URL.createObjectURL = vi.fn(() => "blob:test-file");
HTMLFormElement.prototype.requestSubmit = vi.fn();

afterEach(() => {
  cleanup();
  localStorage.clear();
  sessionStorage.clear();
  document.body.innerHTML = "";
  vi.clearAllMocks();
});
