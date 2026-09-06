import { Empty } from "../components/Empty";

export function Upload() {
  return (
    <div>
      <h1 className="text-lg font-semibold text-slate-900">Upload answer sheets</h1>
      <div className="mt-6">
        <Empty
          title="Upload isn't built yet"
          description="This is where you'll upload scanned answer booklets for evaluation."
        />
      </div>
    </div>
  );
}
