import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import Container from '@cloudscape-design/components/container'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import FormPage from '../components/FormPage.jsx'

export default function SnapshotCreatePage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [searchParams] = useSearchParams()
  const preselectedVolumeId = searchParams.get('volume')

  const [volumes, setVolumes] = useState([])
  const [volume, setVolume]   = useState(null)
  const [name, setName]       = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState('')

  useEffect(() => {
    api.getVolumes().then(r => {
      const items = r?.items || []
      setVolumes(items)
      if (preselectedVolumeId) {
        const match = items.find(v => v.id === preselectedVolumeId)
        if (match) {
          setVolume({ value: match.id, label: match.name })
          setName(`${match.name}-snap`)
        }
      }
    }).catch(() => setVolumes([]))
  }, [preselectedVolumeId])

  const back = () => navigate(preselectedVolumeId ? `/volumes/${preselectedVolumeId}` : '/volumes')

  const submit = async () => {
    if (!volume) { setError('Select a source volume.'); return }
    if (!name) { setError('Snapshot name is required.'); return }
    setLoading(true); setError('')
    try {
      await api.createSnapshot(volume.value, { name })
      toast('Snapshot creation started', 'success')
      back()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <FormPage
      title="Create snapshot"
      description="Point-in-time copies of block storage volumes."
      error={error}
      loading={loading}
      submitText="Create snapshot"
      submitTestId="snap-create-submit"
      onCancel={back}
      onSubmit={submit}
    >
      <Container>
        <SpaceBetween size="l">
          <FormField label="Source volume">
            <Select
              selectedOption={volume}
              onChange={({ detail }) => setVolume(detail.selectedOption)}
              options={volumes.map(v => ({ value: v.id, label: v.name, description: `${v.size_gb || v.size || 0} GiB · ${v.status}` }))}
              placeholder="Choose a volume"
              empty="No volumes"
              disabled={!!preselectedVolumeId}
              controlId="snap-volume"
            />
          </FormField>
          <FormField label="Snapshot name">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="snap-2026-01-01" controlId="snap-name" autoFocus />
          </FormField>
        </SpaceBetween>
      </Container>
    </FormPage>
  )
}
