import { useState, useEffect } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Checkbox from '@cloudscape-design/components/checkbox'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import Modal from '@cloudscape-design/components/modal'
import SpaceBetween from '@cloudscape-design/components/space-between'

export default function DeleteModal({
  visible = true,
  title = 'Delete resource',
  confirmButtonText = 'Delete',
  description,
  itemCount = 1,
  itemName,
  requireText = true,
  requireCheckbox = false,
  checkboxLabel = 'I understand that this action is permanent and will delete this resource and all objects/data contained inside it.',
  loading = false,
  onDismiss,
  onConfirm,
}) {
  const [confirmText, setConfirmText] = useState('')
  const [checked, setChecked] = useState(false)

  useEffect(() => {
    if (visible) {
      setConfirmText('')
      setChecked(false)
    }
  }, [visible])

  const textValid = !requireText || confirmText.trim().toLowerCase() === 'delete'
  const isValid = textValid && (!requireCheckbox || checked)

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
              {confirmButtonText}
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        <Box variant="p">
          {detailText} {description || 'This action cannot be undone.'}
        </Box>
        {requireCheckbox && (
          <Checkbox
            checked={checked}
            onChange={({ detail }) => setChecked(detail.checked)}
          >
            {checkboxLabel}
          </Checkbox>
        )}
        {requireText && (
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
        )}
      </SpaceBetween>
    </Modal>
  )
}
