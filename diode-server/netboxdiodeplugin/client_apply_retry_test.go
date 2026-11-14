package netboxdiodeplugin_test

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/netboxlabs/diode/diode-server/netboxdiodeplugin"
)

// newApplyTestClient starts a fake plugin whose /bulk-plan-apply/ and
// /bulk-plan/ handlers are given, and returns a client pointed at it.
func newApplyTestClient(t *testing.T, applyHandler, planHandler http.HandlerFunc) *netboxdiodeplugin.Client {
	t.Helper()
	authTokenURL := "/diode/auth/token"
	oauth := newMockOAuth2Server(authTokenURL, requireCredentials("test", "test"), "mocked-token")
	t.Cleanup(oauth.Close)

	mux := http.NewServeMux()
	if applyHandler != nil {
		mux.HandleFunc("/api/diode/bulk-plan-apply/", applyHandler)
	}
	if planHandler != nil {
		mux.HandleFunc("/api/diode/bulk-plan/", planHandler)
	}
	ts := httptest.NewServer(mux)
	t.Cleanup(ts.Close)

	client, err := netboxdiodeplugin.NewClient(netboxdiodeplugin.ClientOptions{
		Logger:            slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError})),
		BaseURL:           fmt.Sprintf("%s/api/diode", ts.URL),
		ClientID:          "test",
		ClientSecret:      "test",
		TokenURL:          oauth.URL + authTokenURL,
		RateLimitRPS:      1000,
		RateLimitBurstRPS: 1000,
		MaxRetries:        3,
	})
	require.NoError(t, err)
	return client
}

func applyRequest() netboxdiodeplugin.BulkPlanApplyRequest {
	return netboxdiodeplugin.BulkPlanApplyRequest{Entities: []netboxdiodeplugin.BulkPlanEntity{
		{ID: "1", ObjectType: "dcim.site", EntityJSON: []byte(`{"site":{"name":"s"}}`)},
	}}
}

// A /bulk-plan-apply that outlives the response timeout is still being
// applied by NetBox. Sending it again races the first request and can create
// the same objects twice, so the client must give up after one attempt.
func TestBulkPlanApplyDoesNotResendAfterTimeout(t *testing.T) {
	cleanUpEnvVars()
	t.Setenv(netboxdiodeplugin.ApplyTimeoutSecondsEnvVarName, "1")

	var hits atomic.Int32
	client := newApplyTestClient(t, func(w http.ResponseWriter, _ *http.Request) {
		hits.Add(1)
		time.Sleep(1500 * time.Millisecond)
		_, _ = w.Write([]byte(`{"results":[]}`))
	}, nil)

	_, err := client.BulkPlanApply(context.Background(), applyRequest())
	require.Error(t, err)
	// Give a wrongly scheduled retry time to arrive before counting.
	time.Sleep(2500 * time.Millisecond)
	assert.Equal(t, int32(1), hits.Load(), "bulk-plan-apply must not be resent after it reached NetBox")
}

// 429/503 mean the server refused the request before processing it, so
// retrying is safe and still wanted.
func TestBulkPlanApplyRetriesWhenRejectedBeforeProcessing(t *testing.T) {
	for _, status := range []int{http.StatusServiceUnavailable, http.StatusTooManyRequests} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			cleanUpEnvVars()
			var hits atomic.Int32
			client := newApplyTestClient(t, func(w http.ResponseWriter, _ *http.Request) {
				if hits.Add(1) == 1 {
					w.WriteHeader(status)
					return
				}
				_, _ = w.Write([]byte(`{"results":[{"id":"1","change_set":{"id":"cs","changes":[]}}]}`))
			}, nil)

			resp, err := client.BulkPlanApply(context.Background(), applyRequest())
			require.NoError(t, err)
			require.Len(t, resp.Results, 1)
			assert.Equal(t, int32(2), hits.Load())
		})
	}
}

// A 500 may come from a request that already wrote part of the batch; it is
// returned to the caller instead of being resent.
func TestBulkPlanApplyDoesNotRetryServerError(t *testing.T) {
	cleanUpEnvVars()
	var hits atomic.Int32
	client := newApplyTestClient(t, func(w http.ResponseWriter, _ *http.Request) {
		hits.Add(1)
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"error":"boom"}`))
	}, nil)

	_, err := client.BulkPlanApply(context.Background(), applyRequest())
	require.Error(t, err)
	assert.Equal(t, int32(1), hits.Load())
}

// The apply timeout is independent from the plan/read timeout: a batch that
// takes longer than NETBOX_DIODE_PLUGIN_API_TIMEOUT_SECONDS still succeeds.
func TestBulkPlanApplyUsesItsOwnTimeout(t *testing.T) {
	cleanUpEnvVars()
	t.Setenv(netboxdiodeplugin.TimeoutSecondsEnvVarName, "1")
	t.Setenv(netboxdiodeplugin.ApplyTimeoutSecondsEnvVarName, "5")

	client := newApplyTestClient(t, func(w http.ResponseWriter, _ *http.Request) {
		time.Sleep(1500 * time.Millisecond)
		_, _ = w.Write([]byte(`{"results":[{"id":"1","change_set":{"id":"cs","changes":[]}}]}`))
	}, nil)

	resp, err := client.BulkPlanApply(context.Background(), applyRequest())
	require.NoError(t, err)
	assert.Len(t, resp.Results, 1)
}

// /bulk-plan only reads, so it keeps retrying on timeout as before.
func TestBulkPlanStillRetriesAfterTimeout(t *testing.T) {
	cleanUpEnvVars()
	t.Setenv(netboxdiodeplugin.TimeoutSecondsEnvVarName, "1")

	var hits atomic.Int32
	client := newApplyTestClient(t, nil, func(w http.ResponseWriter, _ *http.Request) {
		if hits.Add(1) == 1 {
			time.Sleep(1500 * time.Millisecond)
		}
		_, _ = w.Write([]byte(`{"results":[]}`))
	})

	_, err := client.BulkPlan(context.Background(), netboxdiodeplugin.BulkPlanRequest{})
	require.NoError(t, err)
	assert.GreaterOrEqual(t, hits.Load(), int32(2))
}

func TestNewClientRejectsInvalidApplyTimeout(t *testing.T) {
	for _, v := range []string{"0", "-3", "abc"} {
		t.Run(v, func(t *testing.T) {
			cleanUpEnvVars()
			t.Setenv(netboxdiodeplugin.ApplyTimeoutSecondsEnvVarName, v)
			_, err := netboxdiodeplugin.NewClient(netboxdiodeplugin.ClientOptions{
				BaseURL: "http://localhost", ClientID: "a", ClientSecret: "b",
				TokenURL: "http://localhost/token", RateLimitRPS: 1, RateLimitBurstRPS: 1,
			})
			assert.ErrorIs(t, err, netboxdiodeplugin.ErrInvalidTimeout)
		})
	}
}
