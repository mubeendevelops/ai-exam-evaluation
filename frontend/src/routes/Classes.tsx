import { Empty } from "../components/Empty";

export function Classes() {
  return (
    <div>
      <h1 className="text-lg font-semibold text-slate-900">Classes & students</h1>
      <div className="mt-6">
        <Empty
          title="Classes & students aren't built yet"
          description="This is where you'll manage exams, classes, and student rosters."
        />
      </div>
    </div>
  );
}
