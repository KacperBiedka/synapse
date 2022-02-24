# Copyright 2016 OpenMarket Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from twisted.internet import defer
from twisted.internet.defer import CancelledError, Deferred

from synapse.util.async_helpers import ReadWriteLock

from tests import unittest


class ReadWriteLockTestCase(unittest.TestCase):
    def _assert_called_before_not_after(self, lst, first_false):
        for i, d in enumerate(lst[:first_false]):
            self.assertTrue(d.called, msg="%d was unexpectedly false" % i)

        for i, d in enumerate(lst[first_false:]):
            self.assertFalse(
                d.called, msg="%d was unexpectedly true" % (i + first_false)
            )

    def test_rwlock(self):
        rwlock = ReadWriteLock()

        key = object()

        ds = [
            rwlock.read(key),  # 0
            rwlock.read(key),  # 1
            rwlock.write(key),  # 2
            rwlock.write(key),  # 3
            rwlock.read(key),  # 4
            rwlock.read(key),  # 5
            rwlock.write(key),  # 6
        ]
        ds = [defer.ensureDeferred(d) for d in ds]

        self._assert_called_before_not_after(ds, 2)

        with ds[0].result:
            self._assert_called_before_not_after(ds, 2)
        self._assert_called_before_not_after(ds, 2)

        with ds[1].result:
            self._assert_called_before_not_after(ds, 2)
        self._assert_called_before_not_after(ds, 3)

        with ds[2].result:
            self._assert_called_before_not_after(ds, 3)
        self._assert_called_before_not_after(ds, 4)

        with ds[3].result:
            self._assert_called_before_not_after(ds, 4)
        self._assert_called_before_not_after(ds, 6)

        with ds[5].result:
            self._assert_called_before_not_after(ds, 6)
        self._assert_called_before_not_after(ds, 6)

        with ds[4].result:
            self._assert_called_before_not_after(ds, 6)
        self._assert_called_before_not_after(ds, 7)

        with ds[6].result:
            pass

        d = defer.ensureDeferred(rwlock.write(key))
        self.assertTrue(d.called)
        with d.result:
            pass

        d = defer.ensureDeferred(rwlock.read(key))
        self.assertTrue(d.called)
        with d.result:
            pass

    def test_cancellation_with_read_lock(self):
        """Test cancellation while holding a read lock."""
        rwlock = ReadWriteLock()
        key = "key"

        # 1. A reader takes the lock and blocks.
        async def reader():
            async with rwlock.read(key):
                await Deferred()

        reader_d = defer.ensureDeferred(reader())

        # 2. A writer waits for the reader to complete.
        async def writer():
            async with rwlock.write(key):
                return "write completed"

        writer_d = defer.ensureDeferred(writer())
        self.assertFalse(writer_d.called)

        # 3. The reader is cancelled.
        reader_d.cancel()
        self.failureResultOf(reader_d, CancelledError)

        # 4. The writer should complete.
        self.assertTrue(
            writer_d.called, "Writer is blocked waiting for a cancelled reader"
        )
        self.assertEqual("write completed", self.successResultOf(writer_d))

    def test_cancellation_with_write_lock(self):
        """Test cancellation while holding a write lock."""
        rwlock = ReadWriteLock()
        key = "key"

        # 1. A writer takes the lock and blocks.
        async def writer():
            async with rwlock.write(key):
                await Deferred()

        writer_d = defer.ensureDeferred(writer())

        # 2. A reader waits for the writer to complete.
        async def reader():
            async with rwlock.read(key):
                return "read completed"

        reader_d = defer.ensureDeferred(reader())
        self.assertFalse(reader_d.called)
        writer_d.cancel()

        # 3. The writer is cancelled.
        self.failureResultOf(writer_d, CancelledError)

        # 4. The reader should complete.
        self.assertTrue(
            reader_d.called, "Reader is blocked waiting for a cancelled writer"
        )
        self.assertEqual("read completed", self.successResultOf(reader_d))

    def test_cancellation_waiting_for_read_lock(self):
        """Test cancellation while waiting for a read lock."""
        rwlock = ReadWriteLock()
        key = "key"

        # 1. A writer takes the lock and blocks.
        writer_block: Deferred[None] = Deferred()

        async def writer1():
            async with rwlock.write(key):
                await writer_block
            return "write 1 completed"

        writer1_d = defer.ensureDeferred(writer1())

        # 2. A reader waits for the first writer to complete.
        #    This reader will be cancelled later.
        async def reader():
            async with rwlock.read(key):
                await Deferred()

        reader_d = defer.ensureDeferred(reader())
        self.assertFalse(reader_d.called)

        # 3. A second writer waits for both the first writer and the reader to complete.
        async def writer2():
            async with rwlock.write(key):
                return "write 2 completed"

        writer2_d = defer.ensureDeferred(writer2())
        self.assertFalse(writer2_d.called)

        # 4. The waiting reader is cancelled.
        #    Neither of the writers should be cancelled.
        #    The second writer should still be blocked, but only on the first writer.
        reader_d.cancel()
        self.failureResultOf(reader_d, CancelledError)
        self.assertFalse(writer1_d.called, "First writer was unexpectedly cancelled")
        self.assertFalse(
            writer2_d.called, "Second writer was unexpectedly unblocked or cancelled"
        )

        # 5. Unblock the first writer, which should complete.
        writer_block.callback(None)
        self.assertEqual("write 1 completed", self.successResultOf(writer1_d))

        # 6. The second writer should be unblocked and complete.
        self.assertTrue(
            writer2_d.called, "Second writer is blocked waiting for a cancelled reader"
        )
        self.assertEqual("write 2 completed", self.successResultOf(writer2_d))

    def test_cancellation_waiting_for_write_lock(self):
        """Test cancellation while waiting for a write lock."""
        rwlock = ReadWriteLock()
        key = "key"

        # 1. A reader takes the lock and blocks.
        reader_block: Deferred[None] = Deferred()

        async def reader():
            async with rwlock.read(key):
                await reader_block
            return "read completed"

        reader_d = defer.ensureDeferred(reader())

        # 2. A writer waits for the first reader to complete.
        #    This writer will be cancelled later.
        async def writer1():
            async with rwlock.write(key):
                await Deferred()

        writer1_d = defer.ensureDeferred(writer1())
        self.assertFalse(writer1_d.called)

        # 3. A second writer waits for both the reader and first writer to complete.
        async def writer2():
            async with rwlock.write(key):
                return "write 2 completed"

        writer2_d = defer.ensureDeferred(writer2())
        self.assertFalse(writer2_d.called)

        # 4. The waiting writer is cancelled.
        #    The reader and second writer should not be cancelled.
        #    The second writer should still be blocked, but only on the reader.
        writer1_d.cancel()
        self.failureResultOf(writer1_d, CancelledError)
        self.assertFalse(reader_d.called, "Reader was unexpectedly cancelled")
        self.assertFalse(
            writer2_d.called, "Second writer was unexpectedly unblocked or cancelled"
        )

        # 5. Unblock the reader, which should complete.
        reader_block.callback(None)
        self.assertEqual("read completed", self.successResultOf(reader_d))

        # 6. The second writer should be unblocked and complete.
        self.assertTrue(
            writer2_d.called, "Second writer is blocked waiting for a cancelled writer"
        )
        self.assertEqual("write 2 completed", self.successResultOf(writer2_d))
