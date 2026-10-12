export default function StateNotice({ loading, error, empty }: { loading: boolean; error: string | null; empty: boolean }) {
  const text = loading ? 'Loading…' : error ? `Could not load data (${error})` : empty ? 'No clusters yet.' : ''
  return (
    <div role="status" className="rounded-[18px] border border-seed-line bg-white p-10 text-center text-base text-seed-muted shadow-[0_16px_44px_rgba(58,22,112,0.16)]">
      {text}
    </div>
  )
}
