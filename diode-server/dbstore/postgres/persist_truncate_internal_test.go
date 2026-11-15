package postgres

import (
	"context"
	"errors"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/netboxlabs/diode/diode-server/reconciler/ops"
)

func int32Ptr(v int32) *int32 { return &v }

func TestPersistThenTruncate_TruncateErrorKeepsCommittedResults(t *testing.T) {
	committed := []ops.BulkPersistResult{
		{IngestionLogID: 1, ChangeSetID: int32Ptr(10)},
		{IngestionLogID: 2, ChangeSetID: int32Ptr(11)},
	}
	var truncatedWith []int

	results, err := persistThenTruncate(context.Background(),
		func() ([]ops.BulkPersistResult, []int, error) { return committed, []int{0, 1}, nil },
		func(withChanges []int) error {
			truncatedWith = withChanges
			return errors.New("ERROR: deadlock detected (SQLSTATE 40P01)")
		},
	)

	require.NoError(t, err, "a post-commit truncate failure must not be reported as a persist failure")
	assert.Equal(t, committed, results)
	assert.Equal(t, []int{0, 1}, truncatedWith)
}

func TestPersistThenTruncate_PersistErrorSkipsTruncate(t *testing.T) {
	persistErr := errors.New("failed to commit transaction")
	truncateCalled := false

	results, err := persistThenTruncate(context.Background(),
		func() ([]ops.BulkPersistResult, []int, error) { return nil, nil, persistErr },
		func([]int) error { truncateCalled = true; return nil },
	)

	require.ErrorIs(t, err, persistErr)
	assert.Nil(t, results)
	assert.False(t, truncateCalled)
}

func TestPersistThenTruncate_Success(t *testing.T) {
	committed := []ops.BulkPersistResult{{IngestionLogID: 7, ChangeSetID: int32Ptr(70)}}

	results, err := persistThenTruncate(context.Background(),
		func() ([]ops.BulkPersistResult, []int, error) { return committed, []int{0}, nil },
		func([]int) error { return nil },
	)

	require.NoError(t, err)
	assert.Equal(t, committed, results)
}
