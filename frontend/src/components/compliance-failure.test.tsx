import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ComplianceFailure } from "./compliance-failure";

describe("failed compliance result", () => {
  it("marks the preserved image rejected and exposes audit detail without retrying", () => {
    render(<ComplianceFailure detail={{
      message: "Generated image failed post-render compliance.",
      render_status: "failed_compliance",
      compliance_status: "failed_compliance",
      hard_compliance_failure: true,
      hard_failure_codes: ["camera_changed"],
      failure_codes: ["camera_changed"],
      failure_reasons: ["The room camera was not preserved"],
      violations: ["door blocked"],
      warnings: ["low confidence"],
      generated_design_id: "design-audit-123",
      generated_image: {
        filename: "rejected.png",
        local_path: "ignored",
        image_url: "/uploads/generated_designs/rejected.png",
        preserved: true,
      },
      automatic_paid_retry_enabled: false,
      automatic_retry_attempted: false,
      retry_policy: "disabled_requires_explicit_enablement",
    }} />);

    expect(screen.getByRole("heading", { name: "Design Not Approved" })).toBeInTheDocument();
    const image = screen.getByRole("img", { name: "Complete rejected generated design" });
    const comments = screen.getByRole("heading", { name: "Compliance comments" });
    expect(image).toHaveClass("compliance-failure-image");
    expect(image).not.toHaveClass("opacity-75");
    expect(image.compareDocumentPosition(comments) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText(/shown in full for review/i)).toBeInTheDocument();
    expect(screen.getByText("design-audit-123")).toBeInTheDocument();
    expect(screen.getByText(/No automatic retry was attempted/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });
});
