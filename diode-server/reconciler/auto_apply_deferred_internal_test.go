package reconciler

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/mock"

	"github.com/netboxlabs/diode/diode-server/reconciler/mocks"
	"github.com/netboxlabs/diode/diode-server/reconciler/ops"
)

// A deferred batch (NetBox refused it before processing, e.g. the default
// branch is syncing) makes the worker wait RetryAfter before it claims again,
// is recorded as a deferred batch with its reason, and is not recorded as a
// failed change set create/apply.
func TestAutoApplyProcessorWaitsRetryAfterOnDeferredBatch(t *testing.T) {
	mockOps := mocks.NewIngestionProcessorOps(t)
	mockMetrics := mocks.NewMetrics(t) // any other Record* call fails the test
	p := NewAutoApplyProcessor(newShutdownTestLogger(), Config{}, nil, mockOps, mockMetrics, nil)

	batch := []ops.QueuedIngestionLog{
		{ID: 1, IngestionLog: newShutdownTestIngestionLog()},
		{ID: 2, IngestionLog: newShutdownTestIngestionLog()},
	}
	mockOps.EXPECT().DefaultBranch(mock.Anything).Return(nil, nil)
	mockOps.EXPECT().BulkPlanApply(mock.Anything, batch, "").Return([]ops.BulkPlanApplyResult{
		{IngestionLogID: 1, Deferred: true, RetryAfter: 300 * time.Millisecond, DeferReason: ops.DeferReasonUnavailable},
		{IngestionLogID: 2, Deferred: true, RetryAfter: 300 * time.Millisecond, DeferReason: ops.DeferReasonUnavailable},
	})
	mockMetrics.EXPECT().RecordBulkApplyDeferred(mock.Anything, ops.DeferReasonUnavailable, int64(2)).Once()

	start := time.Now()
	p.processBatch(context.Background(), batch)
	assert.GreaterOrEqual(t, time.Since(start), 300*time.Millisecond)
}

// The wait ends early when the processor is stopped.
func TestAutoApplyProcessorDeferredWaitHonoursCancel(t *testing.T) {
	mockOps := mocks.NewIngestionProcessorOps(t)
	mockMetrics := mocks.NewMetrics(t)
	p := NewAutoApplyProcessor(newShutdownTestLogger(), Config{}, nil, mockOps, mockMetrics, nil)

	batch := []ops.QueuedIngestionLog{{ID: 1, IngestionLog: newShutdownTestIngestionLog()}}
	mockOps.EXPECT().DefaultBranch(mock.Anything).Return(nil, nil)
	mockOps.EXPECT().BulkPlanApply(mock.Anything, batch, "").Return([]ops.BulkPlanApplyResult{
		{IngestionLogID: 1, Deferred: true, RetryAfter: time.Minute, DeferReason: ops.DeferReasonBranchUnusable},
	})
	mockMetrics.EXPECT().RecordBulkApplyDeferred(mock.Anything, ops.DeferReasonBranchUnusable, int64(1)).Once()

	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	start := time.Now()
	p.processBatch(ctx, batch)
	assert.Less(t, time.Since(start), 5*time.Second)
}
