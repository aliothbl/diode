package reconciler_test

import (
	"context"
	"log/slog"
	"os"
	"testing"
	"time"

	"github.com/stretchr/testify/mock"
	"github.com/stretchr/testify/require"

	"github.com/netboxlabs/diode/diode-server/gen/diode/v1/diodepb"
	pb "github.com/netboxlabs/diode/diode-server/gen/diode/v1/reconcilerpb"
	"github.com/netboxlabs/diode/diode-server/gen/netbox"
	"github.com/netboxlabs/diode/diode-server/netboxdiodeplugin"
	pluginmocks "github.com/netboxlabs/diode/diode-server/netboxdiodeplugin/mocks"
	"github.com/netboxlabs/diode/diode-server/reconciler"
	"github.com/netboxlabs/diode/diode-server/reconciler/mocks"
	reconops "github.com/netboxlabs/diode/diode-server/reconciler/ops"
)

func unavailableTestItems() []reconops.QueuedIngestionLog {
	e := &diodepb.Entity{Entity: &diodepb.Entity_Site{Site: &diodepb.Site{Name: "s"}}}
	return []reconops.QueuedIngestionLog{
		{ID: 1, IngestionLog: &pb.IngestionLog{Id: "log-1", ObjectType: netbox.SiteObjectType, Entity: e}},
		{ID: 2, IngestionLog: &pb.IngestionLog{Id: "log-2", ObjectType: netbox.SiteObjectType, Entity: e}, RequeuedFromState: pb.State_APPLIED},
	}
}

// netbox-branching answers 503 + Retry-After while the default branch syncs
// or merges (F6). The batch must go back to QUEUED -- no FAILED, no failure
// placeholder change set -- and the results must tell the processor to wait.
func TestOpsBulkPlanApplyTransientUnavailableRequeues(t *testing.T) {
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	mockRepository := mocks.NewRepository(t)
	mockNetBoxClient := pluginmocks.NewNetBoxAPI(t)
	opsInstance := reconciler.NewOps(mockRepository, mockNetBoxClient, logger, nil)

	mockNetBoxClient.EXPECT().BulkPlanApply(mock.Anything, mock.Anything).Return(nil,
		&netboxdiodeplugin.UnavailableError{StatusCode: 503, RetryAfter: 7 * time.Second, Retryable: true})
	mockRepository.EXPECT().RequeueApplyingIngestionLogs(mock.Anything, []int32{1, 2}).Return(nil)
	// mocks fail the test on any unexpected call: no UpdateIngestionLogStateWithError
	// (FAILED) and no CreateChangeSet (failure placeholder).

	results := opsInstance.BulkPlanApply(context.Background(), unavailableTestItems(), "branch-1")
	require.Len(t, results, 2)
	for _, r := range results {
		require.True(t, r.Deferred)
		require.Equal(t, 7*time.Second, r.RetryAfter)
		require.Equal(t, reconops.DeferReasonUnavailable, r.DeferReason)
		require.NoError(t, r.PlanErr)
		require.NoError(t, r.ApplyErr)
	}
}

// Without a Retry-After the processor still waits a default pause.
func TestOpsBulkPlanApplyTransientUnavailableDefaultPause(t *testing.T) {
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	mockRepository := mocks.NewRepository(t)
	mockNetBoxClient := pluginmocks.NewNetBoxAPI(t)
	opsInstance := reconciler.NewOps(mockRepository, mockNetBoxClient, logger, nil)

	mockNetBoxClient.EXPECT().BulkPlanApply(mock.Anything, mock.Anything).Return(nil,
		&netboxdiodeplugin.UnavailableError{StatusCode: 503, Retryable: true})
	mockRepository.EXPECT().RequeueApplyingIngestionLogs(mock.Anything, mock.Anything).Return(nil)

	results := opsInstance.BulkPlanApply(context.Background(), unavailableTestItems()[:1], "branch-1")
	require.True(t, results[0].Deferred)
	require.Equal(t, 5*time.Second, results[0].RetryAfter)
}

// 409 {"retryable": false}: the default branch is merged/archived/... Fail
// closed: nothing FAILED, rows requeued, a long pause, and a default branch
// refresh requested.
func TestOpsBulkPlanApplyUnusableBranchFailsClosed(t *testing.T) {
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError + 4}))
	mockRepository := mocks.NewRepository(t)
	mockNetBoxClient := pluginmocks.NewNetBoxAPI(t)
	opsInstance := reconciler.NewOps(mockRepository, mockNetBoxClient, logger, nil)

	mockNetBoxClient.EXPECT().BulkPlanApply(mock.Anything, mock.Anything).Return(nil,
		&netboxdiodeplugin.UnavailableError{StatusCode: 409, Retryable: false})
	mockRepository.EXPECT().RequeueApplyingIngestionLogs(mock.Anything, []int32{1, 2}).Return(nil)

	results := opsInstance.BulkPlanApply(context.Background(), unavailableTestItems(), "branch-1")
	require.Len(t, results, 2)
	for _, r := range results {
		require.True(t, r.Deferred)
		require.Equal(t, 30*time.Second, r.RetryAfter)
		require.Equal(t, reconops.DeferReasonBranchUnusable, r.DeferReason)
	}
}
