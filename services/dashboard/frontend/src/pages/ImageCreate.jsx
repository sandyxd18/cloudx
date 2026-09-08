import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Alert from '@cloudscape-design/components/alert'
import Container from '@cloudscape-design/components/container'
import FileUpload from '@cloudscape-design/components/file-upload'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import ProgressBar from '@cloudscape-design/components/progress-bar'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import FormPage from '../components/FormPage.jsx'

const DISK_FORMATS = [
  { value: 'qcow2', label: 'QCOW2' },
  { value: 'raw',   label: 'RAW' },
  { value: 'vmdk',  label: 'VMDK' },
  { value: 'iso',   label: 'ISO' },
]

const VISIBILITIES = [
  { value: 'public',  label: 'Public' },
  { value: 'private', label: 'Private' },
]

export default function ImageCreatePage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [files, setFiles]   = useState([])
  const [name, setName]     = useState('')
  const [format, setFormat] = useState(DISK_FORMATS[0])
  const [visibility, setVisibility] = useState(VISIBILITIES[0])
  const [progress, setProgress] = useState(null)
  const [error, setError]   = useState('')

  const file = files[0]

  const handleFiles = (selected) => {
    setFiles(selected)
    const f = selected[0]
    if (!f) return
    if (!name) setName(f.name.replace(/\.(qcow2|img|raw|vmdk|iso)$/i, ''))
    const ext = f.name.split('.').pop().toLowerCase()
    const match = DISK_FORMATS.find(d => d.value === (ext === 'img' ? 'raw' : ext))
    if (match) setFormat(match)
  }

  const back = () => navigate('/images')
  const uploading = progress !== null && progress >= 0 && progress < 100

  const submit = async () => {
    if (!file) { setError('Select an image file to upload.'); return }
    if (!name.trim()) { setError('Image name is required.'); return }
    setError('')
    setProgress(0)
    const fd = new FormData()
    fd.append('file', file)
    fd.append('name', name.trim())
    fd.append('disk_format', format.value)
    fd.append('container_format', 'bare')
    fd.append('visibility', visibility.value)
    try {
      await api.uploadImageWithProgress(fd, pct => setProgress(pct))
      setProgress(100)
      toast('Image uploaded successfully', 'success')
      back()
    } catch (e) {
      setError(e.message)
      setProgress(null)
    }
  }

  return (
    <FormPage
      title="Upload image"
      description="Boot images available to launch instances from."
      error={error}
      loading={uploading}
      submitText="Upload image"
      submitTestId="image-upload-submit"
      submitDisabled={!file}
      onCancel={back}
      onSubmit={submit}
    >
      <Container>
        <SpaceBetween size="l">
          <FormField label="Image file" description="Accepted formats: .qcow2, .img, .raw, .vmdk, .iso">
            <FileUpload
              value={files}
              onChange={({ detail }) => handleFiles(detail.value)}
              accept=".qcow2,.img,.raw,.vmdk,.iso"
              i18nStrings={{
                uploadButtonText: () => 'Choose file',
                dropzoneText: () => 'Drop image file to upload',
                removeFileAriaLabel: i => `Remove file ${i + 1}`,
                errorIconAriaLabel: 'Error',
              }}
              showFileSize
              constraintText="Maximum one file per image."
            />
          </FormField>

          {progress !== null && (
            <ProgressBar
              value={progress}
              status={progress >= 100 ? 'success' : 'in-progress'}
              label="Upload progress"
              additionalInfo={progress >= 100 ? 'Upload complete' : `${progress}%`}
            />
          )}

          <FormField label="Image name">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="ubuntu-22.04" disabled={uploading} autoFocus />
          </FormField>

          <FormField label="Disk format">
            <Select selectedOption={format} onChange={({ detail }) => setFormat(detail.selectedOption)} options={DISK_FORMATS} disabled={uploading} />
          </FormField>

          <FormField label="Visibility">
            <Select selectedOption={visibility} onChange={({ detail }) => setVisibility(detail.selectedOption)} options={VISIBILITIES} disabled={uploading} />
          </FormField>
        </SpaceBetween>
      </Container>
    </FormPage>
  )
}
