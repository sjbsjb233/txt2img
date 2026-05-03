// Admin approval queue. Mirrors backend/app/api/admin/approvals.py.

import { apiFetch } from "../client.js";

export const listDeletionRequests = (params = {}) => {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  const suffix = qs.toString() ? `?${qs}` : "";
  return apiFetch(`/api/admin/approvals/deletion${suffix}`);
};

export const approveDeletion = (id, body = {}) =>
  apiFetch(`/api/admin/approvals/deletion/${id}/approve`, {
    method: "POST",
    body,
  });

export const rejectDeletion = (id, body = {}) =>
  apiFetch(`/api/admin/approvals/deletion/${id}/reject`, {
    method: "POST",
    body,
  });
