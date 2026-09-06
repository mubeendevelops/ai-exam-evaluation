import { Empty } from "../components/Empty";

export function QuestionBank() {
  return (
    <div>
      <h1 className="text-lg font-semibold text-slate-900">Question bank</h1>
      <div className="mt-6">
        <Empty
          title="The question bank isn't built yet"
          description="This is where you'll browse, generate, and review questions before they go live."
        />
      </div>
    </div>
  );
}
