export function EconTheory({ title = "经济学解读", theory }: { title?: string; theory: string }) {
  const paragraphs = theory.split("\n\n").filter(Boolean);

  return (
    <div className="mt-8 rounded-lg border border-border p-5">
      <h2 className="text-base font-semibold">{title}</h2>
      <div className="mt-3 space-y-3 text-sm leading-relaxed text-muted-foreground">
        {paragraphs.map((p, i) => (
          <p key={i}>{p}</p>
        ))}
      </div>
    </div>
  );
}
