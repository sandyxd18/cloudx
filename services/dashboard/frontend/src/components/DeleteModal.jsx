import { useState, useEffect } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import Modal from '@cloudscape-design/components/modal'
import SpaceBetween from '@cloudscape-design/components/space-between'

export default function DeleteModal({
  visible = true,
  title = 'Delete resource',
  description,
  itemCount = 1,
  itemName,
  loading = false,
  onDismiss,
  onConfirm,
}) {
  const [confirmText, setConfirmText] = useState('')

  useEffect(() => {
    if (visible) setConfirmText('')
  }, [visible])

  const isValid = confirmText.trim().toLowerCase() === 'delete'

  const handleConfirm = () => {
    if (!isValid || loading) return
    onConfirm()
  }

  const detailText = itemName
    ? `Are you sure you want to delete "${itemName}"?`
    : `Are you sure you want to delete ${itemCount} selected item(s)?`

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      header={title}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss} disabled={loading}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!isValid}
              loading={loading}
              onClick={handleConfirm}
              data-testid="delete-modal-confirm"
            >
              Delete
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        <Box variant="p">
          {detailText} {description || 'This action cannot be undone.'}
        </Box>
        <FormField
          label='To confirm deletion, type "delete" below.'
        >
          <Input
            value={confirmText}
            onChange={({ detail }) => setConfirmText(detail.value)}
            placeholder="delete"
            autoFocus
            controlId="delete-modal-input"
          />
        </FormField>
      </SpaceBetween>
    </Modal>
  )
}
