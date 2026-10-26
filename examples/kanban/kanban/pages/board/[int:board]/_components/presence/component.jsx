export function Presence({ count = 0, members = [] }) {
  if (count === 0) return null;
  const guests = Math.max(0, count - members.length);
  return (
    <section
      aria-label="Watching this board"
      data-kanban-presence
      className="flex items-center gap-3 text-xs text-slate-600"
    >
      <span>Watching now</span>
      <ul className="flex -space-x-2">
        {members.map((member, index) => (
          <li
            key={member.key ?? `${index}-${member.name}`}
            title={member.name}
            data-kanban-member={member.name}
            className="inline-flex h-7 w-7 items-center justify-center rounded-full border-2 border-white bg-slate-800 font-medium text-white"
          >
            <span aria-hidden="true">{member.initials}</span>
            <span className="sr-only">{member.name}</span>
          </li>
        ))}
      </ul>
      {guests > 0 && (
        <span data-kanban-guests>
          {members.length > 0 ? "+" : ""}
          {guests} {guests === 1 ? "guest" : "guests"}
        </span>
      )}
    </section>
  );
}
