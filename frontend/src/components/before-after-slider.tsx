"use client";

import { MoveHorizontal } from "lucide-react";
import { useState } from "react";
import { mediaUrl } from "@/lib/api";

const BEFORE = "/uploads/rooms/aef775ac-ea3c-47ed-8e7f-cf4a16ca7f88.jpg";
const AFTER = "/uploads/generated_designs/generated_design_1_9c5f47a1-93af-4c34-bd68-760a67f4a4fd.png";

export function BeforeAfterSlider() {
  const [position, setPosition] = useState(52);

  return (
    <div className="before-after" style={{ "--position": `${position}%` } as React.CSSProperties}>
      {/* Both images are locally stored Aqar AI project media. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={mediaUrl(AFTER) || ""} alt="Furnished room design preview" className="before-after-image" />
      <div className="before-layer">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={mediaUrl(BEFORE) || ""} alt="Original empty room" className="before-after-image" />
      </div>
      <div className="before-after-line">
        <span><MoveHorizontal size={18} /></span>
      </div>
      <span className="compare-label left-4">Original room</span>
      <span className="compare-label right-4">Design preview</span>
      <input
        aria-label="Compare original and furnished room"
        type="range"
        min="8"
        max="92"
        value={position}
        onChange={(event) => setPosition(Number(event.target.value))}
        className="compare-range"
      />
    </div>
  );
}
