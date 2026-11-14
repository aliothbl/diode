package postgres

import (
	"testing"

	"github.com/stretchr/testify/assert"

	"github.com/netboxlabs/diode/diode-server/reconciler/ops"
)

// UPDATE ... RETURNING hands claimed rows back in hash order; the batch
// must reach the plugin in ingestion (id) order.
func TestSortQueuedByIDRestoresIngestionOrder(t *testing.T) {
	batch := []ops.QueuedIngestionLog{{ID: 405}, {ID: 3}, {ID: 2147483000}, {ID: 17}, {ID: 4}}
	sortQueuedByID(batch)

	got := make([]int32, len(batch))
	for i, q := range batch {
		got[i] = q.ID
	}
	assert.Equal(t, []int32{3, 4, 17, 405, 2147483000}, got)
}

func TestSortQueuedByIDHandlesEmptyAndSingle(t *testing.T) {
	sortQueuedByID(nil)
	one := []ops.QueuedIngestionLog{{ID: 9}}
	sortQueuedByID(one)
	assert.Equal(t, int32(9), one[0].ID)
}
