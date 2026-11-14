package postgres

import (
	"context"
	"errors"
	"math/rand/v2"
	"time"

	"github.com/jackc/pgx/v5/pgconn"
)

const (
	// txRetryAttempts bounds how many times a transaction that lost a
	// deadlock (or a serialization conflict) is run again.
	txRetryAttempts = 5

	// txRetryBaseDelay is the first backoff; it doubles per attempt, with
	// jitter so the transactions that formed the cycle do not collide again.
	txRetryBaseDelay = 10 * time.Millisecond
)

// isRetryableTxError reports whether err means Postgres aborted the whole
// transaction to resolve a conflict with another one, so running it again
// from the start is safe and expected to succeed:
//
//   - 40P01 deadlock_detected
//   - 40001 serialization_failure
//
// Errors are matched through wrapping (fmt.Errorf("...: %w", err)).
func isRetryableTxError(err error) bool {
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) {
		return false
	}
	return pgErr.Code == "40P01" || pgErr.Code == "40001"
}

// withTxRetry runs fn, which must open, use and commit (or roll back) its own
// transaction, and runs it again when it fails with a retryable conflict.
//
// The reconciler's transactions deadlock under concurrent auto-apply workers:
// a dedup transaction holds the state-counter rows its INSERT trigger
// touched while BulkMarkDuplicates waits for a row a claim holds, and the
// claim's UPDATE trigger then waits for those counter rows (SQLSTATE 40P01,
// seen with AUTO_APPLY_PROCESSOR_CONCURRENCY=4 in the end-to-end benchmark).
// The counter triggers order their own locks (migration 20260528000001), but
// a lock cycle across statements of different transactions remains possible.
// Postgres resolves it by aborting one transaction; without a retry, a lost
// BulkPersistChangeSets marked a whole applied batch FAILED.
//
// fn must be safe to run more than once: everything it writes is inside the
// transaction that the conflict rolled back.
func withTxRetry(ctx context.Context, fn func() error) error {
	delay := txRetryBaseDelay
	for attempt := 1; ; attempt++ {
		err := fn()
		if err == nil || !isRetryableTxError(err) || attempt >= txRetryAttempts {
			return err
		}
		jitter := time.Duration(rand.Int64N(int64(delay)))
		select {
		case <-ctx.Done():
			return errors.Join(err, ctx.Err())
		case <-time.After(delay + jitter):
		}
		delay *= 2
	}
}
