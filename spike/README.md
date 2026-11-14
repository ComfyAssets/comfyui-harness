# Throwaway spike

Not part of the product. An unauthenticated shell in a ComfyUI page, used once
to check that the terminal works through the author's proxy (plan Task 0).
It does nothing unless `HARNESS_SPIKE=1` is set, and it refuses any caller
that is not on loopback, so behind a proxy it needs the one-line change
described in the plan. Delete this folder when Task 0 is done.
