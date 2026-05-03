// Resolve which request-param a ui_schema field writes to.
//
// A schema field's `k` names the *capability* it reads from
// (e.g. `n_max`); the `value_key` override names the *param* it writes
// to (e.g. `n`). When unset, the param key is the same as `k` — the
// common case for `size`, `aspect_ratio`, `quality`, …
export function paramKey(field) {
  return field.value_key || field.k;
}
