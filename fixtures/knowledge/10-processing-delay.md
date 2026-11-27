id: kb-processing-delay
title: Delayed processing of exports and batch jobs
tags: delay, processing, pending, stuck, queued, export, batch, job, slow
---
Exports (`/v1/exports`) and batch imports (`/v1/batches`) run asynchronously. A job moves through `queued`, `processing`, and then `completed` or `failed`. The job object shows `status`, `created_at` and `progress_percent`.

Normal timing: most jobs finish within 15 minutes. Large exports (more than 1 million rows) can take up to 2 hours.

What support can tell the customer:

- If the job is less than 2 hours old and `progress_percent` is increasing, it is still within normal processing time. Ask them to keep polling `GET /v1/exports/{job_id}` no more often than every 30 seconds.
- If an incident is posted on status.fernwick.example, point them to it and do not give a separate completion estimate.
- Do not promise a completion time.

Ask for the job ID (prefix `job_`) and the creation time. A job that has been `queued` for more than 2 hours, or whose `progress_percent` has not changed for 30 minutes, must go to manual review by the data-pipeline team.
