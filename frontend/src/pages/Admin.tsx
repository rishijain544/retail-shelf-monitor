import { useState, useCallback } from 'react';
import { Store, Camera, listStores, listCameras, createCamera, ApiError } from '../api/client';


function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError;
}


export default function Admin() {
  // Access control lives in <AuthGate> (App.tsx), which redirects to /login when
  // there is no valid session. The backend scopes cameras to the signed-in user's
  // store, so this page never needs to pick a store itself.

  const [stores, setStores] = useState<Store[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [loading, setLoading] = useState(true);
  const [addCameraOpen, setAddCameraOpen] = useState(false);

  // camera form
  const [cameraAisle, setCameraAisle] = useState('');
  const [cameraName, setCameraName] = useState('');
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [cameraSaving, setCameraSaving] = useState(false);

  const loadData = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      // The store list is only used to label cameras in the directory; the API
      // already scopes both lists to the signed-in user's store.
      const [s, c] = await Promise.all([
        listStores(signal),
        listCameras(signal),
      ]);
      setStores(s);
      setCameras(c);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  function openAddCamera() {
    setCameraAisle('');
    setCameraName('');
    setCameraError(null);
    setAddCameraOpen(true);
  }

  async function handleAddCamera() {
    if (!cameraAisle.trim()) {
      setCameraError('Aisle is required.');
      return;
    }
    if (!cameraName.trim()) {
      setCameraError('Camera name is required.');
      return;
    }
    setCameraSaving(true);
    setCameraError(null);
    try {
      // The backend derives store_id from the authenticated user and ignores any
      // client-supplied store_id, so we no longer ask the user to pick a store.
      const created = await createCamera({
        aisle: cameraAisle.trim(),
        name: cameraName.trim(),
      });
      setCameras((prev) => [...prev, created]);
      setAddCameraOpen(false);
    } catch (err) {
      setCameraError(isApiError(err) ? (err.detail ?? err.message) : err instanceof Error ? err.message : String(err));
    } finally {
      setCameraSaving(false);
    }
  }

  return (
    <section className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Store &amp; Camera Directory</h1>
          <p className="text-xs text-gray-500">
            {loading
              ? 'Loading…'
              : `${stores.length} store${stores.length === 1 ? '' : 's'} · ${cameras.length} camera${cameras.length === 1 ? '' : 's'}`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void loadData()}
            className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 transition hover:bg-gray-50"
          >
            Refresh
          </button>
          <button
            type="button"
            onClick={openAddCamera}
            className="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-sky-700"
          >
            Add Camera
          </button>
        </div>
      </header>

      {/* Cameras */}
      <section className="rounded-lg border border-gray-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Cameras</h2>
        {cameras.length === 0 ? (
          <p className="text-xs text-gray-500">No cameras yet. Add one below to start capturing shelves.</p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {cameras.map((camera) => {
              const store = stores.find((s) => s.id === camera.store_id);
              return (
                <li key={camera.id} className="flex items-center justify-between py-2 first:pt-0 last:pb-0">
                  <div>
                    <p className="text-sm font-medium text-gray-900">{camera.name}</p>
                    <p className="text-xs text-gray-500">
                      {store?.name ?? 'Store ' + camera.store_id} · {camera.aisle}
                    </p>
                  </div>
                  <span className="text-xs text-gray-400">ID {camera.id}</span>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* Add camera modal */}
      {addCameraOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/40 p-4" role="dialog" aria-modal="true" aria-labelledby="add-camera-title">
          <div className="w-full max-w-sm rounded-lg border border-gray-200 bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
              <h2 id="add-camera-title" className="text-sm font-semibold text-gray-900">Add Camera</h2>
              <button
                type="button"
                onClick={() => setAddCameraOpen(false)}
                className="rounded p-1 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600"
                aria-label="Close"
              >
                ✕
              </button>
            </div>
            <div className="space-y-4 px-4 py-4">
              <p className="text-xs text-gray-500">
                Cameras are added to your current store automatically.
              </p>
              <div>
                <label className="mb-1 block text-xs font-semibold text-gray-700">Aisle</label>
                <input
                  type="text"
                  value={cameraAisle}
                  onChange={(e) => setCameraAisle(e.target.value)}
                  disabled={cameraSaving}
                  placeholder="e.g. Aisle 7 — Beverages"
                  className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500 disabled:bg-gray-50"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold text-gray-700">Camera name</label>
                <input
                  type="text"
                  value={cameraName}
                  onChange={(e) => setCameraName(e.target.value)}
                  disabled={cameraSaving}
                  placeholder="e.g. cam-front-bev"
                  className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500 disabled:bg-gray-50"
                />
              </div>
              {cameraError && (
                <p className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-700">
                  {cameraError}
                </p>
              )}
              <div className="flex justify-end gap-2 border-t border-gray-100 pt-3">
                <button
                  type="button"
                  onClick={() => setAddCameraOpen(false)}
                  className="rounded-md border border-gray-300 px-3 py-1.5 text-xs font-semibold text-gray-700 transition hover:bg-gray-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleAddCamera}
                  disabled={cameraSaving}
                  className="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-sky-700 disabled:cursor-not-allowed disabled:bg-gray-300"
                >
                  {cameraSaving ? 'Saving…' : 'Add Camera'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
