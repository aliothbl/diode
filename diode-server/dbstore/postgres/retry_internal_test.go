package postgres

import (
	"context"
	"errors"
	"fmt"
	"testing"

	"github.com/jackc/pgx/v5/pgconn"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func pgErr(code string) error { return &pgconn.PgError{Code: code, Message: "test"} }

func TestWithTxRetryRunsAgainAfterDeadlock(t *testing.T) {
	for _, code := range []string{"40P01", "40001"} {
		t.Run(code, func(t *testing.T) {
			calls := 0
			err := withTxRetry(context.Background(), func() error {
				calls++
				if calls < 3 {
					// Repository methods wrap driver errors with %w.
					return fmt.Errorf("failed to bulk update ingestion log states: %w", pgErr(code))
				}
				return nil
			})
			require.NoError(t, err)
			assert.Equal(t, 3, calls)
		})
	}
}

func TestWithTxRetryDoesNotRetryOtherErrors(t *testing.T) {
	for name, e := range map[string]error{
		"unique_violation": pgErr("23505"),
		"plain":            errors.New("boom"),
	} {
		t.Run(name, func(t *testing.T) {
			calls := 0
			err := withTxRetry(context.Background(), func() error { calls++; return e })
			assert.ErrorIs(t, err, e)
			assert.Equal(t, 1, calls)
		})
	}
}

func TestWithTxRetryGivesUpAfterBoundedAttempts(t *testing.T) {
	calls := 0
	err := withTxRetry(context.Background(), func() error { calls++; return pgErr("40P01") })
	require.Error(t, err)
	assert.True(t, isRetryableTxError(err))
	assert.Equal(t, txRetryAttempts, calls)
}

func TestWithTxRetryStopsWhenContextIsCancelled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	calls := 0
	err := withTxRetry(ctx, func() error {
		calls++
		cancel()
		return pgErr("40P01")
	})
	assert.ErrorIs(t, err, context.Canceled)
	assert.Equal(t, 1, calls)
}
