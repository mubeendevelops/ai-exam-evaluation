// Shared error state. Every screen shows API/network failures through this
// component rather than rolling its own — teachers see one consistent
// "something went wrong" shape, and a support request always has a
// request id to search the server log for (RE-5, api/main.py's exception
// handlers).
interface ErrorMessageProps {
  message: string;
  requestId?: string;
  onRetry?: () => void;
}

export function ErrorMessage({ message, requestId, onRetry }: ErrorMessageProps) {
  return (
    <div role="alert" className="flex flex-col items-center gap-3 rounded-lg border border-red-200 bg-red-50 px-6 py-10 text-center">
      <p className="font-medium text-red-800">{message}</p>
      {requestId && (
        <p className="text-xs text-red-600">
          Reference for support: <code className="rounded bg-red-100 px-1 py-0.5">{requestId}</code>
        </p>
      )}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="rounded-md border border-red-300 bg-white px-4 py-1.5 text-sm font-medium text-red-700 hover:bg-red-100"
        >
          Try again
        </button>
      )}
    </div>
  );
}
