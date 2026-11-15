package changeset_test

import (
	"encoding/json"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	diodeErrors "github.com/netboxlabs/diode/diode-server/errors"
	"github.com/netboxlabs/diode/diode-server/reconciler/changeset"
)

func TestNewError_PlainTextDetailsStayMarshallable(t *testing.T) {
	// netbox-branching's middleware answers a branch that is syncing or
	// merging with a plain-text 400 body.
	err := changeset.NewError("bulk plan-apply failed", diodeErrors.ErrCodeOpsApplyChangeSet,
		[]byte("Selected branch is not ready."))

	b, mErr := json.Marshal(err)
	require.NoError(t, mErr)

	var got map[string]any
	require.NoError(t, json.Unmarshal(b, &got))
	assert.Equal(t, "Selected branch is not ready.", got["details"])
	assert.Equal(t, string(diodeErrors.ErrCodeOpsApplyChangeSet), got["code"])
}

func TestNewError_JSONDetailsKeptAsIs(t *testing.T) {
	body := []byte(`{"errors":{"request":{"__all__":["internal error"]}}}`)
	err := changeset.NewError("bulk plan-apply failed", diodeErrors.ErrCodeOpsApplyChangeSet, body)

	var csErr *changeset.Error
	require.ErrorAs(t, err, &csErr)
	assert.JSONEq(t, string(body), string(csErr.Details))

	_, mErr := json.Marshal(err)
	require.NoError(t, mErr)
}

func TestNewError_EmptyDetails(t *testing.T) {
	err := changeset.NewError("x", diodeErrors.ErrCodeOpsApplyChangeSet, nil)
	b, mErr := json.Marshal(err)
	require.NoError(t, mErr)
	assert.NotContains(t, string(b), "details")
}
