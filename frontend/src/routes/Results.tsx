import { Empty } from "../components/Empty";

export function Results() {
  return (
    <div>
      <h1 className="text-lg font-semibold text-slate-900">Results</h1>
      <div className="mt-6">
        <Empty
          title="Results aren't built yet"
          description="This is where you'll review AI-scored answers and confirm or override them."
        />
      </div>
    </div>
  );
}
