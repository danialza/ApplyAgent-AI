interface Props {
  message: string;
  onDismiss?: () => void;
}

export default function ErrorMessage({ message, onDismiss }: Props) {
  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
      <div>
        <p className="font-medium">Something went wrong</p>
        <p className="mt-0.5 text-red-600">{message}</p>
      </div>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          className="text-red-500 hover:text-red-700"
          aria-label="Dismiss"
        >
          ×
        </button>
      )}
    </div>
  );
}
