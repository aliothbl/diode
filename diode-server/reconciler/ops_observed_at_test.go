package reconciler_test

import (
	"context"
	"encoding/json"
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

// SPEC-20261124-1134: every entity of a bulk-plan-apply carries the time its
// content was last observed (the row's last_seen), so the plugin can keep a
// field a newer observation already wrote. A row without one sends nothing.
func TestOpsBulkPlanApplySendsObservedAt(t *testing.T) {
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	mockRepository := mocks.NewRepository(t)
	mockNetBoxClient := pluginmocks.NewNetBoxAPI(t)
	opsInstance := reconciler.NewOps(mockRepository, mockNetBoxClient, logger, nil)

	seen := time.Date(2026, 11, 24, 18, 30, 0, 123_000_000, time.UTC)
	e := &diodepb.Entity{Entity: &diodepb.Entity_Site{Site: &diodepb.Site{Name: "s"}}}
	items := []reconops.QueuedIngestionLog{
		{ID: 1, IngestionLog: &pb.IngestionLog{Id: "log-1", ObjectType: netbox.SiteObjectType, Entity: e}, ObservedAt: seen},
		{ID: 2, IngestionLog: &pb.IngestionLog{Id: "log-2", ObjectType: netbox.SiteObjectType, Entity: e}},
	}

	var sent netboxdiodeplugin.BulkPlanApplyRequest
	mockNetBoxClient.EXPECT().BulkPlanApply(mock.Anything, mock.Anything).
		Run(func(_ context.Context, req netboxdiodeplugin.BulkPlanApplyRequest) { sent = req }).
		Return(nil, &netboxdiodeplugin.UnavailableError{StatusCode: 503, Retryable: true})
	mockRepository.EXPECT().RequeueApplyingIngestionLogs(mock.Anything, mock.Anything).Return(nil)

	opsInstance.BulkPlanApply(context.Background(), items, "")

	require.Len(t, sent.Entities, 2)
	require.Equal(t, seen.UnixMilli(), sent.Entities[0].ObservedAt)
	require.Zero(t, sent.Entities[1].ObservedAt)

	// On the wire: observed_at in epoch milliseconds, absent when unknown.
	raw, err := json.Marshal(sent.Entities[0])
	require.NoError(t, err)
	require.Contains(t, string(raw), `"observed_at":1795545000123`)
	raw, err = json.Marshal(sent.Entities[1])
	require.NoError(t, err)
	require.NotContains(t, string(raw), "observed_at")
}
