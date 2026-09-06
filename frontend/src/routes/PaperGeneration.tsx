import { Empty } from "../components/Empty";

export function PaperGeneration() {
  return (
    <div>
      <h1 className="text-lg font-semibold text-slate-900">Paper generation</h1>
      <div className="mt-6">
        <Empty
          title="Paper generation isn't built yet"
          description="This is where you'll build a paper pattern and generate a paper from live questions."
        />
      </div>
    </div>
  );
}
