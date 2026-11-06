/** Join conditional class names. */
export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(' ')
}
