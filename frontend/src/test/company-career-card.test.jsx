import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import DiscoveredJobCard from "../components/DiscoveredJobCard";

const job = {
  discovered_job_id: 123, title: "Software Engineer", company: "Acme Widgets",
  detected_label: "Recently Detected", source_kind: "company_direct", source_platform: "greenhouse",
  source_label: "Official Company Career Page",
  official_job_url: "https://job-boards.greenhouse.io/acme/jobs/12345",
  application_url: "https://job-boards.greenhouse.io/acme/jobs/12345",
  action_label: "Apply on Company Site",
};

describe("official company discovery cards", () => {
  it("adds source provenance and an official listing without removing existing controls", () => {
    render(<DiscoveredJobCard job={job} userId={1} onSave={vi.fn()} onDetails={vi.fn()} onOptimize={vi.fn()} />);
    expect(screen.getByText("Official Company Career Page")).toBeInTheDocument();
    const listing = screen.getByRole("link", { name: "View Listing" });
    expect(listing).toHaveAttribute("href", job.official_job_url);
    expect(listing).toHaveAttribute("rel", "noopener noreferrer");
    for (const name of ["Save", "Details", "Optimize Resume", "Apply on Company Site"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it.each(["Apply on Company Site", "Open Career Page", "View Listing", "Job Closed"])(
    "preserves backend label %s even when no action URL is present", (label) => {
      render(<DiscoveredJobCard job={{ ...job, action_label: label, application_url: null }} />);
      expect(screen.getByRole("button", { name: label })).toBeDisabled();
    },
  );

  it("does not add official controls to aggregator cards", () => {
    render(<DiscoveredJobCard job={{ ...job, source_kind: "adzuna", source_label: null, official_job_url: null, action_label: "Apply on Adzuna" }} />);
    expect(screen.queryByText("Official Company Career Page")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "View Listing" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply on Adzuna" })).toBeInTheDocument();
  });

  it("does not render an unsafe listing scheme", () => {
    render(<DiscoveredJobCard job={{ ...job, official_job_url: "javascript:alert(1)" }} />);
    expect(screen.queryByRole("link", { name: "View Listing" })).not.toBeInTheDocument();
  });
});
