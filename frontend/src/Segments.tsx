import type { Segment } from "./redline";

const TAGS = { same: "span", removed: "del", inserted: "ins", focus: "mark" } as const;

export default function Segments({ segments }: { segments: Segment[] }) {
  return segments.map((segment, i) => {
    const Tag = TAGS[segment.kind];
    return <Tag key={i}>{segment.text}</Tag>;
  });
}
