export type Action = (
  work: () => Promise<unknown>,
  message: string | ((result: unknown) => string),
) => Promise<void>;
